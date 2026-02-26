# booking_v2

Nueva capa de reservas desacoplada en torno a inventario explícito de slots.

## Qué incluye

- `scripts/create_dynamodb_table.py`: crea la tabla DynamoDB con PK/SK + GSIs necesarias.
- `scripts/publish_slots.py`: publica slots abiertos para un rango de fechas.
- `docs/data-model.md`: contrato de datos (items, claves, índices y flujos).

## Uso rápido

1. Crear la tabla:

```bash
python3 booking_v2/scripts/create_dynamodb_table.py \
  --table-name reservai-booking-v2 \
  --region eu-west-1
```

2. Configurar `.env` para usar la nueva tabla:

```env
DYNAMODB_TABLE_NAME=reservai-booking-v2
```

3. Publicar slots para una semana:

```bash
python3 booking_v2/scripts/publish_slots.py \
  --date-from 2026-03-01 \
  --date-to 2026-03-07 \
  --opened-by ops-weekly-job
```

4. Probar flujo por API local (modo no producción):

- `POST /admin/publish-slots`
- `GET /admin/availability?date=YYYY-MM-DD&people=4&zone=terraza`

## Recomendación operativa

Automatizar `publish_slots.py` con EventBridge Scheduler + Lambda (o job en Railway/GitHub Actions) para abrir ventanas semanalmente.
