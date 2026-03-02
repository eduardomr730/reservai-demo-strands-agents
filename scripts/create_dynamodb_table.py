#!/usr/bin/env python3
"""
Crea la tabla DynamoDB para reservas con PK/SK + GSI1.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


def table_exists(client, table_name: str) -> bool:
    try:
        client.describe_table(TableName=table_name)
        return True
    except client.exceptions.ResourceNotFoundException:
        return False


def create_table(client, table_name: str) -> None:
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": "gsi1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    parser = argparse.ArgumentParser(description="Crear tabla DynamoDB de reservas")
    parser.add_argument(
        "--table-name",
        default=os.getenv("DYNAMODB_TABLE_NAME", "reservai-demo-reservations"),
        help="Nombre de la tabla DynamoDB",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("DYNAMODB_REGION", os.getenv("AWS_REGION", "eu-west-1")),
        help="Región AWS",
    )
    args = parser.parse_args()

    aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    aws_session_token = os.getenv("AWS_SESSION_TOKEN", "")

    client_kwargs = {"region_name": args.region}
    if aws_access_key_id and aws_secret_access_key:
        client_kwargs["aws_access_key_id"] = aws_access_key_id
        client_kwargs["aws_secret_access_key"] = aws_secret_access_key
        if aws_session_token:
            client_kwargs["aws_session_token"] = aws_session_token

    client = boto3.client("dynamodb", **client_kwargs)

    if table_exists(client, args.table_name):
        print(f"✅ La tabla '{args.table_name}' ya existe en {args.region}.")
        return 0

    try:
        create_table(client, args.table_name)
        print(f"🛠️ Creando tabla '{args.table_name}' en {args.region}...")
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=args.table_name)
        print(f"✅ Tabla '{args.table_name}' creada correctamente.")
        return 0
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "UnrecognizedClientException":
            print(
                "❌ Credenciales AWS inválidas. Revisa AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY "
                "y, si son credenciales temporales (STS), añade AWS_SESSION_TOKEN.",
                file=sys.stderr,
            )
            return 1
        print(f"❌ Error creando tabla: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
