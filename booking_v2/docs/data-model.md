# Data Model (booking_v2)

## Tabla

- Nombre: `reservai-booking-v2` (recomendado)
- PK/SK: `PK` (HASH), `SK` (RANGE)
- GSIs: `GSI1`, `GSI2`, `GSI3`

## Entidades

## `slot`

Representa un inventario puntual de disponibilidad (`fecha + hora + mesa`).

- `PK = DAY#YYYY-MM-DD`
- `SK = SLOT#HH:MM#TABLE#<table_id>`
- `status`: `open | held | booked | blocked`
- `reservation_id`: id de la reserva que ocupa el slot
- `capacity_min`, `capacity_max`, `zone`, `priority`
- `GSI1PK = DAY#YYYY-MM-DD#STATUS#<status>`
- `GSI1SK = TIME#HH:MM#TABLE#<table_id>`

## `reservation`

Reserva confirmada o pendiente, separada del inventario.

- `PK = RES#<reservation_id>`
- `SK = META`
- `status`: `pending | confirmed | cancelled`
- `date`, `time`, `num_people`, `customer_name`, `phone`
- `table_id`, `table_zone`, `slot_ref`
- `GSI2PK = DATE#YYYY-MM-DD`
- `GSI2SK = TIME#HH:MM#RES#<reservation_id>`
- `GSI3PK = PHONE#<phone>`
- `GSI3SK = DATE#YYYY-MM-DD#TIME#HH:MM#RES#<reservation_id>`

## `customer_lookup`

Lookup auxiliar por teléfono y fecha/hora.

- `PK = PHONE#<phone>`
- `SK = RES#YYYY-MM-DD#HH:MM#<reservation_id>`

## `table_meta`

Catálogo de mesas activas.

- `PK = TABLE#<table_id>`
- `SK = META`
- `zone`, `capacity_min`, `capacity_max`, `priority`, `is_active`

## Flujos

1. Publicación de slots:
- `publish_slots(date_from, date_to)` inserta `slot` en estado `open`.

2. Creación de reserva:
- Selecciona un `slot open` compatible.
- Transacción DynamoDB:
  - `slot open -> booked`
  - `put reservation`
  - `put customer_lookup`

3. Cancelación o cambio:
- Transacción DynamoDB:
  - liberar slot anterior
  - reservar slot nuevo (si aplica)
  - actualizar reserva
  - actualizar lookup
