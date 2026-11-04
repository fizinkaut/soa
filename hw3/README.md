# Flight Booking — REST, gRPC, PostgreSQL, Redis

Учебная распределённая система бронирования авиабилетов. Реализованы пункты 1–7 задания.

```text
Client --REST--> Booking Service :8000 --gRPC + API Key--> Flight Service :50051
                     |                                          |      |
                 booking_db                                  flight_db Redis
```

Каждый сервис владеет своей PostgreSQL. Booking Service хранит заказы и данные пассажира;
Flight Service управляет рейсами, местами и кешем.

## Запуск

```bash
docker compose up -d --build
docker compose ps
```

Миграции Alembic применяются автоматически. Если каталог пуст, добавляются три учебных
рейса на следующий день (UTC): SVO → LED и VKO → KZN. Существующие рейсы не изменяются.
Отключение примеров: `SEED_DEMO_FLIGHTS=false docker compose up -d`.

REST: http://localhost:8000, Swagger UI: http://localhost:8000/docs.
gRPC: `localhost:50051`. Redis доступен внутри Compose по адресу `redis:6379`.

Остановка: `docker compose down`. Опция `-v` удаляет данные PostgreSQL.

## Настройки

| Переменная | Назначение | Значение в Compose |
|---|---|---|
| `GRPC_API_KEY` | Общий ключ двух сервисов | `local-study-key` для локальной демонстрации |
| `CACHE_TTL_SECONDS` | TTL каждой записи, 300–600 секунд | `300` |
| `REDIS_URL` | Адрес Redis для Flight Service | `redis://redis:6379/0` |
| `DATABASE_URL` | Собственная PostgreSQL сервиса; также используется Alembic | Задан отдельно каждому сервису |
| `FLIGHT_ADDRESS` | Адрес Flight Service для Booking Service | `flight-service:50051` |
| `SEED_DEMO_FLIGHTS` | Добавлять примеры в пустую БД | `true` |

Свой ключ можно задать в `.env` рядом с Compose: `GRPC_API_KEY=your-secret`.
Оба сервиса получают одно значение через окружение. Пустой ключ запрещён.
API Key передаётся в gRPC metadata `x-api-key`; серверный interceptor проверяет **все четыре метода**.
Без ключа или с неверным ключом ответ — `UNAUTHENTICATED`.

## REST API

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/flights?origin=SVO&destination=LED&date=2026-11-05` | Поиск только SCHEDULED; date необязателен |
| GET | `/flights/{id}` | Получение рейса, 404 при отсутствии |
| POST | `/bookings` | Создание бронирования |
| GET | `/bookings/{id}` | Получение бронирования, 404 при отсутствии |
| GET | `/bookings?user_id=demo` | Список бронирований пользователя |
| POST | `/bookings/{id}/cancel` | Отмена; повторная отмена безопасна |

Пример для существующего рейса с ID 1:

```bash
curl 'http://localhost:8000/flights?origin=SVO&destination=LED'
curl -X POST http://localhost:8000/bookings \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","flight_id":1,"passenger_name":"Ivan Ivanov","passenger_email":"ivan@example.com","seat_count":2}'
curl -X POST http://localhost:8000/bookings/BOOKING_UUID/cancel
```

Создание: GetFlight → ReserveSeats → запись CONFIRMED, HTTP 201.
`total_price = seat_count × flight.price` — снимок цены на момент получения рейса.
При неудачном резервировании booking не создаётся.

## gRPC-контракт и ошибки

Контракт: [proto/flight.proto](proto/flight.proto). Python-модули генерируются из него
через `grpc_tools.protoc` при сборке Docker-образа. Статусы передаются enum;
моменты вылета и прилёта — `google.protobuf.Timestamp`. Календарная дата поиска и
`departure_date` — строка YYYY-MM-DD, поскольку это дата без времени и часового пояса.

| Метод | Бизнес-ошибки |
|---|---|
| Все | UNAUTHENTICATED — неверный или отсутствующий API Key |
| SearchFlights | INVALID_ARGUMENT — неверные IATA-коды или дата |
| GetFlight | INVALID_ARGUMENT — ID ≤ 0; NOT_FOUND — нет рейса |
| ReserveSeats | INVALID_ARGUMENT — неверные UUID/ID/count; NOT_FOUND — нет рейса; RESOURCE_EXHAUSTED — мало мест; ALREADY_EXISTS — UUID с другими параметрами; FAILED_PRECONDITION — рейс не SCHEDULED либо резервация отменена |
| ReleaseReservation | INVALID_ARGUMENT — неверный UUID; FAILED_PRECONDITION — резервация EXPIRED |

ReleaseReservation идемпотентен. Для неизвестного UUID тоже возвращается успех:
сохраняется запрет позднего резервирования с этим UUID. Это нужно для компенсации
после потерянного ответа ReserveSeats.

REST преобразует NOT_FOUND в 404, INVALID_ARGUMENT в 400, конфликты в 409,
UNAVAILABLE в 503, DEADLINE_EXCEEDED в 504. Ошибка межсервисного ключа — 502,
поскольку исправлять нужно настройки сервисов, а не данные REST-клиента.

## Транзакции и восстановление

Во Flight Service резервирование блокирует рейс через SELECT FOR UPDATE.
Уменьшение мест, создание SeatReservation и смена версии кеша выполняются в одной
транзакции. Возврат мест и перевод в RELEASED также атомарны. Повтор ReserveSeats с
тем же UUID и параметрами не списывает места снова; изменённые параметры запрещены.
Резервирование и освобождение одного UUID дополнительно упорядочены транзакционной
advisory-блокировкой PostgreSQL.

До ReserveSeats Booking Service сохраняет задачу компенсации в своей БД. Затем
блокирует её на время запроса. Запись успешного booking и удаление задачи — одна
транзакция. При ошибке или перезапуске задача остаётся: фоновый обработчик возвращает
места. Аналогично сохраняется намерение отменить booking: после сбоя обработчик
завершает отмену. Он просыпается раз в 5 секунд и берёт одну задачу старше 30 секунд;
активные запросы пропускает через FOR UPDATE SKIP LOCKED.

Между БД нет общей ACID-транзакции: при недоступности Flight Service временно возможен
резерв без booking либо незавершённая отмена. Сохранённая задача обеспечивает
восстановление после возвращения сервиса; до восстановления задача не теряется.
Это не retry обычного запроса из пункта 8, а обработка сохранённой компенсации.

## Redis Cache-Aside

GetFlight и SearchFlights: проверка Redis → при miss чтение рейсов из PostgreSQL →
запись protobuf-ответа в Redis. Есть INFO-логи `cache hit` / `cache miss`.
Каждый ключ создаётся с TTL 300 секунд (настраивается в пределах 5–10 минут).
Примеры: `flight:1:v4`, `search:SVO:LED:all:v4`.

В PostgreSQL есть одна строка `cache_state` с версией данных. Перед обращением к кешу
сервис читает эту версию; каждое изменение мест увеличивает её **в той же транзакции**.
Поэтому ReserveSeats и ReleaseReservation сразу инвалидируют все предыдущие ключи,
включая поиск с датой и без неё. Старые ключи больше не читаются и удаляются по TTL.
Так запоздавшее чтение не может записать старые данные в актуальную версию кеша.
Недоступность Redis не мешает работе: сервис читает PostgreSQL, а после восстановления
Redis не возвращается к старой версии. Цена решения — небольшой запрос версии к БД
даже при cache hit и сброс кеша всех маршрутов после любой мутации.

UpdateFlight не входит в обязательные четыре метода и не добавлен. Если он появится,
нужно вызвать `cache.invalidate(cursor)` в транзакции обновления. Прямые ручные UPDATE
рейсов также должны увеличивать `cache_state.revision` в своей транзакции.

## Демонстрация

Все команды ниже выполняются **в Bash внутри WSL**, последовательно в одном терминале.
Нужны Docker Compose, curl и python3. Устанавливать Python-зависимости на хост не нужно:
gRPC-примеры выполняются внутри контейнера.

### 1. Запуск

```bash
cd /home/laxus/soa/hw3
docker compose up -d --build
docker compose ps
```

Должны работать пять контейнеров: две PostgreSQL, Redis и два сервиса.
Миграции выполняются при запуске; на пустой базе появляются три учебных рейса.
После запуска дайте сервисам несколько секунд на применение миграций.

Откройте http://localhost:8000/docs — здесь можно показывать REST API через Swagger.
ER-диаграмма находится в `docs/erd.md`: откройте Markdown-предпросмотр с поддержкой Mermaid.

Проверка версий миграций:

```bash
docker compose exec flight-service alembic -c flight_service/alembic.ini current
docker compose exec booking-service alembic -c booking_service/alembic.ini current
```

Ожидаются `a001_cache_state (head)` и `b001_compensations (head)`.

### 2. Поиск и получение рейса

```bash
curl -fsS 'http://localhost:8000/flights?origin=SVO&destination=LED' | python3 -m json.tool

FLIGHT_ID=$(curl -fsS 'http://localhost:8000/flights?origin=SVO&destination=LED' | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')
curl -fsS "http://localhost:8000/flights/$FLIGHT_ID" | python3 -m json.tool
```

`FLIGHT_ID` берётся из ответа автоматически. Если база ранее заполнялась вручную и
маршрута SVO → LED нет, выберите origin и destination существующего рейса.

Для поиска с датой скопируйте дату из выбранного рейса автоматически:

```bash
FLIGHT_DATE=$(curl -fsS "http://localhost:8000/flights/$FLIGHT_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["departure_date"])')
curl -fsS "http://localhost:8000/flights?origin=SVO&destination=LED&date=$FLIGHT_DATE" | python3 -m json.tool
```

Поиск возвращает только SCHEDULED. Запомните available_seats и price выбранного рейса.

### 3. Создание бронирования

Рейс должен иметь минимум два свободных места.

```bash
BOOKING_JSON=$(curl -fsS -X POST 'http://localhost:8000/bookings' \
  -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"demo\",\"flight_id\":$FLIGHT_ID,\"passenger_name\":\"Ivan Ivanov\",\"passenger_email\":\"ivan@example.com\",\"seat_count\":2}")
python3 -m json.tool <<< "$BOOKING_JSON"
BOOKING_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<< "$BOOKING_JSON")

curl -fsS "http://localhost:8000/flights/$FLIGHT_ID" | python3 -m json.tool
curl -fsS "http://localhost:8000/bookings/$BOOKING_ID" | python3 -m json.tool
curl -fsS 'http://localhost:8000/bookings?user_id=demo' | python3 -m json.tool
```

Ожидается CONFIRMED; total_price = 2 × price; available_seats уменьшилось на два.
Booking Service получил цену через GetFlight, зарезервировал места через ReserveSeats
и записал заказ в свою PostgreSQL.

### 4. Отмена и повторная отмена

```bash
curl -fsS -X POST "http://localhost:8000/bookings/$BOOKING_ID/cancel" | python3 -m json.tool
curl -fsS "http://localhost:8000/flights/$FLIGHT_ID" | python3 -m json.tool

curl -fsS -X POST "http://localhost:8000/bookings/$BOOKING_ID/cancel" | python3 -m json.tool
curl -fsS "http://localhost:8000/flights/$FLIGHT_ID" | python3 -m json.tool
```

Статус CANCELLED, места вернулись. После повторной отмены число мест не увеличивается.

### 5. Ошибка при нехватке мест

```bash
curl -i -X POST 'http://localhost:8000/bookings' \
  -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"demo-no-seats\",\"flight_id\":$FLIGHT_ID,\"passenger_name\":\"Ivan Ivanov\",\"passenger_email\":\"ivan@example.com\",\"seat_count\":2147483647}"

curl -fsS 'http://localhost:8000/bookings?user_id=demo-no-seats' | python3 -m json.tool
```

На учебных рейсах мест меньше запрошенного: ответ HTTP 409, список заказов пустой.

### 6. Аутентификация всех gRPC-методов

Выполните блок целиком. Ключ для успешных вызовов берётся из окружения контейнера;
запросы с неверным ключом не выполняют бизнес-операций.

```bash
docker compose exec -T booking-service python - <<'PY'
import os
import uuid
import grpc
from booking_service.flight_client import client
from proto import flight_pb2 as pb

requests = [
    (client.SearchFlights, pb.SearchFlightsRequest(origin="SVO", destination="LED")),
    (client.GetFlight, pb.GetFlightRequest(flight_id=1)),
    (client.ReserveSeats, pb.ReserveSeatsRequest(flight_id=1, booking_id=str(uuid.uuid4()), seat_count=1)),
    (client.ReleaseReservation, pb.ReleaseReservationRequest(booking_id=str(uuid.uuid4()))),
]
for method, request in requests:
    for label, metadata in [("no key", ()), ("wrong key", (("x-api-key", "wrong-key"),))]:
        try:
            method(request, timeout=5, metadata=metadata)
            print(label, "unexpected success")
        except grpc.RpcError as exc:
            print(request.DESCRIPTOR.name, label, exc.code().name)

response = client.SearchFlights(
    pb.SearchFlightsRequest(origin="SVO", destination="LED"),
    timeout=5, metadata=(("x-api-key", os.environ["GRPC_API_KEY"]),),
)
print("Correct key: search succeeded, flights:", len(response.flights))
PY
```

Ожидаются восемь ответов UNAUTHENTICATED, затем успешный поиск с правильным ключом.

### 7. Redis: hit/miss, TTL и инвалидация

```bash
curl -fsS "http://localhost:8000/flights/$FLIGHT_ID" > /dev/null
curl -fsS "http://localhost:8000/flights/$FLIGHT_ID" > /dev/null
curl -fsS 'http://localhost:8000/flights?origin=SVO&destination=LED' > /dev/null
curl -fsS 'http://localhost:8000/flights?origin=SVO&destination=LED' > /dev/null
docker compose logs --tail 30 flight-service
docker compose exec redis redis-cli --scan
```

В логах видны cache hit и cache miss. Если запись уже была в кеше, оба чтения могут
дать hit. Для гарантированного miss выполните ещё раз создание или отмену из шагов
3–4, затем повторите чтение: версия кеша изменится, первое чтение даст miss, второе — hit.
Число мест в GetFlight и результатах поиска должно соответствовать изменению.

Посмотреть TTL всех текущих ключей без ручного копирования имён:

```bash
docker compose exec -T flight-service python - <<'PY'
from flight_service.cache import client
for key in sorted(client.scan_iter()):
    print(key.decode(), "TTL:", client.ttl(key), "seconds")
PY
```

У записей есть положительный TTL до 300 секунд. Старые версии ключей больше не
используются и исчезают по TTL — это логическая инвалидация по версии данных.

### 8. Логи и остановка

Логи обоих сервисов (Ctrl+C прекращает просмотр, контейнеры продолжают работать):

```bash
docker compose logs -f booking-service flight-service
```

После демонстрации:

```bash
docker compose down
```

Данные сохранятся. `docker compose down -v` удалит базы — для обычной демонстрации
эта команда не нужна.

Схема и ограничения: [docs/erd.md](docs/erd.md).
Пункты 8–10 (retry, Sentinel/Cluster и Circuit Breaker) в этот объём не входят.

Проверено вручную на запущенных контейнерах: все четыре RPC отклоняют отсутствующий
и неверный ключ; создание и повторная отмена; фиксация цены; нехватка мест без записи
booking; два конкурентных запроса на остаток мест; идемпотентность и конфликт UUID;
TTL и hit/miss; инвалидация поиска и получения рейса; сохранённые компенсации;
работа REST при остановленном Redis и после его восстановления. Миграции с нуля
и повторное заполнение примеров проверены на отдельных временных БД.

В Compose у Flight Service ограничено ожидание DNS (timeout=1, attempts=1),
а повторные попытки Redis-клиента отключены: недоступный кеш не должен исчерпывать
5-секундный срок gRPC-вызова.
