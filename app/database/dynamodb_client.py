"""
Cliente de DynamoDB para gestionar reservas.
"""
import boto3
import logging
from typing import Optional, List, Dict
from datetime import datetime
from decimal import Decimal
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


class DynamoDBClient:
    """Cliente para interactuar con DynamoDB."""
    
    def __init__(self):
        self.dynamodb = boto3.resource(
            'dynamodb',
            region_name=settings.dynamodb_region
        )
        self.table = self.dynamodb.Table(settings.dynamodb_table_name)
        logger.info(f"✅ DynamoDB client inicializado para tabla: {settings.dynamodb_table_name}")
    
    def _python_to_dynamodb(self, data: dict) -> dict:
        """Convierte tipos Python a tipos DynamoDB (int -> Decimal)."""
        converted = {}
        for key, value in data.items():
            if isinstance(value, int):
                converted[key] = Decimal(str(value))
            elif isinstance(value, float):
                converted[key] = Decimal(str(value))
            elif isinstance(value, dict):
                converted[key] = self._python_to_dynamodb(value)
            else:
                converted[key] = value
        return converted
    
    def _dynamodb_to_python(self, data: dict) -> dict:
        """Convierte tipos DynamoDB a tipos Python (Decimal -> int/float)."""
        converted = {}
        for key, value in data.items():
            if isinstance(value, Decimal):
                # Si es entero, convertir a int, sino a float
                converted[key] = int(value) if value % 1 == 0 else float(value)
            elif isinstance(value, dict):
                converted[key] = self._dynamodb_to_python(value)
            else:
                converted[key] = value
        return converted
    
    def put_reservation(self, reservation: dict) -> bool:
        """Crear o actualizar una reserva."""
        try:
            # Construir items con patrón de acceso
            reservation_id = reservation['id']
            phone = reservation['phone']
            date = reservation['date']
            time = reservation['time']
            
            # Añadir timestamps
            now = datetime.utcnow().isoformat()
            if 'created_at' not in reservation:
                reservation['created_at'] = now
            reservation['updated_at'] = now
            
            # Convertir tipos
            item = self._python_to_dynamodb(reservation)
            
            # Item principal
            main_item = {
                'PK': f'RESERVATION#{reservation_id}',
                'SK': 'METADATA',
                'GSI1PK': f'DATE#{date}',
                'GSI1SK': f'TIME#{time}',
                **item
            }
            
            # Escribir item principal
            self.table.put_item(Item=main_item)
            
            # Item de índice por cliente
            customer_item = {
                'PK': f'CUSTOMER#{phone}',
                'SK': f'RESERVATION#{date}#{time}',
                'reservation_id': reservation_id,
                'date': date,
                'time': time,
                'status': reservation['status']
            }
            
            self.table.put_item(Item=customer_item)
            
            logger.info(f"✅ Reserva guardada: {reservation_id}")
            return True
            
        except ClientError as e:
            logger.error(f"❌ Error guardando reserva: {e.response['Error']['Message']}")
            return False
    
    def get_reservation(self, reservation_id: str) -> Optional[dict]:
        """Obtener una reserva por ID."""
        try:
            response = self.table.get_item(
                Key={
                    'PK': f'RESERVATION#{reservation_id}',
                    'SK': 'METADATA'
                }
            )
            
            if 'Item' in response:
                item = self._dynamodb_to_python(response['Item'])
                # Limpiar claves de DynamoDB
                item.pop('PK', None)
                item.pop('SK', None)
                item.pop('GSI1PK', None)
                item.pop('GSI1SK', None)
                return item
            
            return None
            
        except ClientError as e:
            logger.error(f"❌ Error obteniendo reserva: {e.response['Error']['Message']}")
            return None
    
    def query_reservations_by_date(self, date: str) -> List[dict]:
        """Obtener todas las reservas de una fecha."""
        try:
            response = self.table.query(
                IndexName='GSI1',
                KeyConditionExpression=Key('GSI1PK').eq(f'DATE#{date}')
            )
            
            items = [self._dynamodb_to_python(item) for item in response.get('Items', [])]
            
            # Limpiar y devolver solo reservas reales
            reservations = []
            for item in items:
                item.pop('PK', None)
                item.pop('SK', None)
                item.pop('GSI1PK', None)
                item.pop('GSI1SK', None)
                reservations.append(item)
            
            return reservations
            
        except ClientError as e:
            logger.error(f"❌ Error consultando por fecha: {e.response['Error']['Message']}")
            return []
    
    def query_reservations_by_status(self, status: str, date: Optional[str] = None) -> List[dict]:
        """Obtener reservas por estado, opcionalmente filtradas por fecha."""
        try:
            if date:
                # Consulta con fecha específica
                response = self.table.query(
                    IndexName='StatusDateIndex',
                    KeyConditionExpression=Key('status').eq(status) & Key('date').eq(date)
                )
            else:
                # Consulta solo por estado
                response = self.table.query(
                    IndexName='StatusDateIndex',
                    KeyConditionExpression=Key('status').eq(status)
                )
            
            items = [self._dynamodb_to_python(item) for item in response.get('Items', [])]
            
            reservations = []
            for item in items:
                # Solo incluir items que son reservas reales (tienen SK=METADATA)
                if item.get('SK') == 'METADATA':
                    item.pop('PK', None)
                    item.pop('SK', None)
                    item.pop('GSI1PK', None)
                    item.pop('GSI1SK', None)
                    reservations.append(item)
            
            return reservations
            
        except ClientError as e:
            logger.error(f"❌ Error consultando por estado: {e.response['Error']['Message']}")
            return []
    
    def query_customer_reservations(self, phone: str) -> List[dict]:
        """Obtener todas las reservas de un cliente."""
        try:
            response = self.table.query(
                KeyConditionExpression=Key('PK').eq(f'CUSTOMER#{phone}')
            )
            
            # Obtener IDs de reservas
            reservation_ids = [
                item['reservation_id'] 
                for item in response.get('Items', [])
                if 'reservation_id' in item
            ]
            
            # Obtener detalles completos de cada reserva
            reservations = []
            for res_id in reservation_ids:
                reservation = self.get_reservation(res_id)
                if reservation:
                    reservations.append(reservation)
            
            return reservations
            
        except ClientError as e:
            logger.error(f"❌ Error consultando reservas de cliente: {e.response['Error']['Message']}")
            return []
    
    def update_reservation(self, reservation_id: str, updates: dict) -> bool:
        """Actualizar campos específicos de una reserva."""
        try:
            # Primero obtener la reserva actual
            current = self.get_reservation(reservation_id)
            if not current:
                return False
            
            # Combinar con updates
            updated_reservation = {**current, **updates}
            updated_reservation['updated_at'] = datetime.utcnow().isoformat()
            
            # Guardar
            return self.put_reservation(updated_reservation)
            
        except Exception as e:
            logger.error(f"❌ Error actualizando reserva: {e}")
            return False
    
    def delete_reservation(self, reservation_id: str) -> bool:
        """Eliminar una reserva (soft delete: cambiar status a cancelled)."""
        try:
            return self.update_reservation(reservation_id, {'status': 'cancelled'})
        except Exception as e:
            logger.error(f"❌ Error eliminando reserva: {e}")
            return False
    
    def scan_all_reservations(self, status_filter: Optional[str] = None) -> List[dict]:
        """
        Escanear todas las reservas (usar con cuidado, costoso).
        Solo para admin/stats.
        """
        try:
            scan_kwargs = {
                'FilterExpression': Attr('SK').eq('METADATA')
            }
            
            if status_filter:
                scan_kwargs['FilterExpression'] = (
                    Attr('SK').eq('METADATA') & Attr('status').eq(status_filter)
                )
            
            response = self.table.scan(**scan_kwargs)
            items = response.get('Items', [])
            
            # Paginación si hay más resultados
            while 'LastEvaluatedKey' in response:
                response = self.table.scan(
                    ExclusiveStartKey=response['LastEvaluatedKey'],
                    **scan_kwargs
                )
                items.extend(response.get('Items', []))
            
            reservations = []
            for item in items:
                item = self._dynamodb_to_python(item)
                item.pop('PK', None)
                item.pop('SK', None)
                item.pop('GSI1PK', None)
                item.pop('GSI1SK', None)
                reservations.append(item)
            
            return reservations
            
        except ClientError as e:
            logger.error(f"❌ Error escaneando reservas: {e.response['Error']['Message']}")
            return []


# Instancia global
db_client = DynamoDBClient()