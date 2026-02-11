"""DynamoDB client wrapper used by repositories."""

from __future__ import annotations

import logging
from typing import Any

import boto3

from app.config import settings

logger = logging.getLogger(__name__)


class DynamoDBClient:
    """Thin wrapper around boto3 DynamoDB table operations."""

    def __init__(self) -> None:
        self.resource = boto3.resource("dynamodb", region_name=settings.dynamodb_region)
        self.table = self.resource.Table(settings.dynamodb_table_name)
        logger.info("DynamoDB client ready for table: %s", settings.dynamodb_table_name)

    def get_item(self, key: dict[str, Any]) -> dict[str, Any] | None:
        try:
            response = self.table.get_item(Key=key)
            return response.get("Item")
        except Exception as exc:  # noqa: BLE001
            logger.error("DynamoDB get_item failed: %s", str(exc))
            return None

    def put_item(
        self,
        item: dict[str, Any],
        *,
        condition_expression: str | None = None,
        expression_attribute_values: dict[str, Any] | None = None,
    ) -> bool:
        kwargs: dict[str, Any] = {"Item": item}
        if condition_expression:
            kwargs["ConditionExpression"] = condition_expression
        if expression_attribute_values:
            kwargs["ExpressionAttributeValues"] = expression_attribute_values

        try:
            self.table.put_item(**kwargs)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("DynamoDB put_item failed: %s", str(exc))
            return False

    def delete_item(
        self,
        key: dict[str, Any],
        *,
        condition_expression: str | None = None,
        expression_attribute_values: dict[str, Any] | None = None,
    ) -> bool:
        kwargs: dict[str, Any] = {"Key": key}
        if condition_expression:
            kwargs["ConditionExpression"] = condition_expression
        if expression_attribute_values:
            kwargs["ExpressionAttributeValues"] = expression_attribute_values

        try:
            self.table.delete_item(**kwargs)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("DynamoDB delete_item failed: %s", str(exc))
            return False

    def query(self, **kwargs: Any) -> list[dict[str, Any]]:
        try:
            response = self.table.query(**kwargs)
            items = response.get("Items", [])

            while "LastEvaluatedKey" in response:
                response = self.table.query(ExclusiveStartKey=response["LastEvaluatedKey"], **kwargs)
                items.extend(response.get("Items", []))

            return items
        except Exception as exc:  # noqa: BLE001
            logger.error("DynamoDB query failed: %s", str(exc))
            return []

    def scan(self, **kwargs: Any) -> list[dict[str, Any]]:
        try:
            response = self.table.scan(**kwargs)
            items = response.get("Items", [])

            while "LastEvaluatedKey" in response:
                response = self.table.scan(ExclusiveStartKey=response["LastEvaluatedKey"], **kwargs)
                items.extend(response.get("Items", []))

            return items
        except Exception as exc:  # noqa: BLE001
            logger.error("DynamoDB scan failed: %s", str(exc))
            return []


# Global singleton
db_client = DynamoDBClient()
