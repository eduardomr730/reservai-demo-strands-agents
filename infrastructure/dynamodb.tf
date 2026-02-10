terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "eu-west-1"
}

resource "aws_dynamodb_table" "reservations" {
  name           = "restaurant-reservations"
  billing_mode   = "PAY_PER_REQUEST"  # Escalado automático
  hash_key       = "PK"
  range_key      = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "GSI1PK"
    type = "S"
  }

  attribute {
    name = "GSI1SK"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "date"
    type = "S"
  }

  # GSI para buscar por fecha
  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }

  # GSI para buscar por estado y fecha
  global_secondary_index {
    name            = "StatusDateIndex"
    hash_key        = "status"
    range_key       = "date"
    projection_type = "ALL"
  }

  # Habilitar TTL para auto-limpieza (opcional)
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  # Encriptación en reposo
  server_side_encryption {
    enabled = true
  }

  # Point-in-time recovery (backups)
  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Environment = "production"
    Project     = "rincon-andalucia"
    ManagedBy   = "terraform"
  }
}

# Output para usar en la app
output "dynamodb_table_name" {
  value = aws_dynamodb_table.reservations.name
}

output "dynamodb_table_arn" {
  value = aws_dynamodb_table.reservations.arn
}