# Marketplace: OpenAPI + CRUD

API маркетплейса по `homework.md`: FastAPI, Pydantic 2, PostgreSQL,
Flyway, JWT (access 20 минут, refresh 14 дней), роли USER / SELLER / ADMIN.

## Запуск

```sh
docker compose up --build
```

API: <http://localhost:8000>, Swagger UI: <http://localhost:8000/docs>.
PostgreSQL: `localhost:5433`, база / пользователь / пароль: `marketplace`.
Контейнер `migrate` применяет Flyway-миграции перед запуском API. Данные сохраняются
в Docker volume. Порты привязаны к localhost.

## Генерация и локальная разработка

```sh
python3 -m venv .venv
make install
make generate
docker compose up -d db migrate
.venv/bin/uvicorn app.main:app --reload --no-access-log
```

`make install` использует `uv`; эквивалент без него:
`.venv/bin/python -m pip install -r requirements-dev.txt`.
Нужен Python 3.13 или новее. При локальном запуске API остановите его контейнер,
если порт 8000 уже занят: `docker compose stop api`.

Источник контракта — `openapi/marketplace.yaml`. `make generate` одной командой
запускает `scripts/generate.py`, который генерирует только Pydantic DTO через
datamodel-code-generator. Результат в `app/generated/` исключён из Git. Dockerfile
выполняет эту же генерацию при сборке. Ручных DTO нет. FastAPI-хэндлеры написаны
вручную в `app/routes.py` и используют сгенерированные модели. `/openapi.json`
отдаёт исходный контракт, который также используется Swagger UI.

Дополнительные ограничения `x-unique-by` (повторяющиеся товары) и `x-after`
(интервал промокода) берутся из контракта и проверяются Pydantic до бизнес-логики.
`x-roles` описывает разрешённые роли в контракте; хэндлеры проверяют их через `require_roles`.

## Проверка

```sh
make install
make test
```

Тесты используют отдельную БД `marketplace_test`, созданную и мигрированную Flyway.
Таблицы этой тестовой БД очищаются между тестами; основная БД не затрагивается.
Проверяются контракт, CRUD, ошибки, ограничения, JWT, роли, состояния, промокоды,
откат транзакций и конкурирующие запросы.

## Устройство

- `app/routes.py` — FastAPI-хэндлеры, параметры запросов и проверка ролей.
- `app/services/` — бизнес-логика, SQL и проверки прав владельца.
- `app/db.py` — транзакция на запрос. Зависимости имеют `scope="function"`,
  поэтому commit/rollback завершается до отправки ответа.
- `app/security.py` — Argon2id, JWT и авторизация; актуальная роль сверяется с БД.
- `app/main.py` — единые ошибки, JSON-логи, маскирование паролей и токенов.
- `migrations/V1__marketplace.sql` — схема, ограничения, индексы и timestamp-триггеры.

Для ограничения частоты блокируется строка текущего пользователя. Остатки
блокируются `SELECT ... FOR UPDATE` в одинаковом порядке UUID; промокод также
блокируется перед изменением счётчика. Дополнительный частичный уникальный индекс
запрещает два активных заказа одного пользователя. Неуспешные операции не
попадают в `user_operations`.

`ORDER_INTERVAL_MINUTES` задаёт N (по умолчанию 1). Например:
`ORDER_INTERVAL_MINUTES=2 docker compose up --build`.
`DATABASE_URL` и `JWT_SECRET` настраиваются переменными окружения; в Compose
уже задано подключение к контейнеру БД.

Регистрация разрешает USER и SELLER; ADMIN назначается через SQL.
Для ADMIN есть `POST /orders/{id}/status`, реализующий требуемую цепочку
`CREATED → PAYMENT_PENDING → PAID → SHIPPED → COMPLETED`.
Отмена из CREATED / PAYMENT_PENDING выполняется только через `/cancel`.

Деньги считаются в Decimal с округлением скидки HALF_UP до копеек.
Процентная скидка свыше 70% равна нулю (полная цена).
При обновлении сохранённые позиции сохраняют снимок цены, новые получают текущую цену.
Собственное использование промокода не мешает обновлять заказ при `max_uses=1`.
Падение суммы ниже минимума снимает промокод; истечение / отключение / исчерпание
промокода при обновлении возвращает ошибку и откатывает изменения.
