#!/usr/bin/env python3
"""Create DynamoDB table for booking_v2."""

from __future__ import annotations

import argparse
import time

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create booking_v2 DynamoDB table")
    parser.add_argument("--table-name", required=True, help="DynamoDB table name")
    parser.add_argument("--region", default="eu-west-1", help="AWS region")
    return parser.parse_args()


def create_table(client: boto3.client, table_name: str) -> None:
    try:
        client.describe_table(TableName=table_name)
        print(f"Table {table_name} already exists")
        return
    except client.exceptions.ResourceNotFoundException:
        pass

    print(f"Creating table {table_name}...")

    client.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"},
            {"AttributeName": "GSI3PK", "AttributeType": "S"},
            {"AttributeName": "GSI3SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "GSI2",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "GSI3",
                "KeySchema": [
                    {"AttributeName": "GSI3PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI3SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        StreamSpecification={"StreamEnabled": False},
    )

    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=table_name)

    while True:
        response = client.describe_table(TableName=table_name)
        status = response["Table"]["TableStatus"]
        if status == "ACTIVE":
            break
        time.sleep(2)

    print(f"Table {table_name} is ACTIVE")


def main() -> None:
    args = parse_args()
    client = boto3.client("dynamodb", region_name=args.region)

    try:
        create_table(client, args.table_name)
    except ClientError as exc:
        message = exc.response.get("Error", {}).get("Message", str(exc))
        raise SystemExit(f"Failed to create table: {message}") from exc


if __name__ == "__main__":
    main()
