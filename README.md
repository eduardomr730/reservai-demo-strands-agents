# reservai-demo-strands-agents
This repository contains the source code for a WhatsApp bot assistant for a virtual restaurant. The bot is powered by AI agents built with `strands-agents` and AWS Bedrock, allowing it to handle reservations, answer questions about the menu, and provide restaurant information in a conversational manner.

The application is built with FastAPI and is designed for easy deployment using Docker, with specific configurations for platforms like Railway.

## ✨ Features

- **Conversational AI:** Utilizes `strands-agents` and Amazon Bedrock (Anthropic Claude Sonnet) to understand and respond to user queries in natural language.
- **WhatsApp Integration:** Seamlessly integrates with WhatsApp through the Twilio API.
- **Reservation Management:** Uses explicit open slots in DynamoDB and transactional reservation updates (no double-booking).
- **Slot Publishing:** Auto-publishes 30-minute slots on demand from restaurant opening windows.
- **Persistent Memory:** Employs `bedrock-agentcore` for persistent memory, enabling contextually aware conversations across multiple interactions with the same user.
- **Information Hub:** Provides detailed information about the restaurant's menu, operating hours, and location, based on a comprehensive system prompt.
- **Containerized:** Includes a `Dockerfile` and Gunicorn configuration for robust, production-ready deployment.
- **Scalable:** Built on FastAPI and designed to run with multiple workers.

## 🛠️ Technology Stack

- **Backend Framework:** FastAPI
- **AI Agent Framework:** `strands-agents`
- **Cloud AI Service:** Amazon Web Services (AWS) Bedrock
- **Messaging API:** Twilio for WhatsApp
- **Database:** DynamoDB (single-table model with slots + reservations)
- **Deployment:** Docker, Gunicorn, Uvicorn
- **Dependency Management:** `uv`

## 🚀 Getting Started

Follow these instructions to set up and run the project on your local machine.

### Prerequisites

- Python 3.12+
- `uv` (pipx install uv) or `pip`
- A Twilio account with a configured WhatsApp sandbox or number
- AWS credentials with access to Bedrock

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/eduardomr730/reservai-demo-strands-agents.git
    cd reservai-demo-strands-agents
    ```

2.  **Install dependencies using `uv`:**
    ```bash
    uv pip install -e .
    ```

### Configuration

1.  **Create an environment file:**
    Copy the example environment file to create your own configuration.
    ```bash
    cp .env.example .env
    ```

2.  **Edit the `.env` file:**
    Fill in the placeholder values with your actual credentials and configuration:
    - `TWILIO_AUTH_TOKEN`: Your Twilio authentication token.
    - `AWS_REGION`: The AWS region where your Bedrock models are available (e.g., `us-east-1`).
    - `AWS_ACCESS_KEY_ID`: Your AWS access key.
    - `AWS_SECRET_ACCESS_KEY`: Your AWS secret key.
    - `AGENTCORE_MEMORY_ID`: The ID for your AgentCore memory instance.

### DynamoDB Table Setup

Create one DynamoDB table with a primary key (`PK`, `SK`) and one GSI for phone lookup:

```bash
aws dynamodb create-table \
  --table-name reservai-booking-v2 \
  --attribute-definitions \
      AttributeName=PK,AttributeType=S \
      AttributeName=SK,AttributeType=S \
      AttributeName=GSI1PK,AttributeType=S \
      AttributeName=GSI1SK,AttributeType=S \
  --key-schema \
      AttributeName=PK,KeyType=HASH \
      AttributeName=SK,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --global-secondary-indexes '[
    {
      "IndexName":"gsi1",
      "KeySchema":[
        {"AttributeName":"GSI1PK","KeyType":"HASH"},
        {"AttributeName":"GSI1SK","KeyType":"RANGE"}
      ],
      "Projection":{"ProjectionType":"ALL"}
    }
  ]'
```

Or create it with Python (same schema):

```bash
python3 scripts/create_dynamodb_table.py --table-name reservai-demo-reservations --region eu-west-1
```

## 🏃‍♀️ Usage

### Running Locally

To run the application in a local development environment with hot-reloading:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The server will be available at `http://localhost:8000`.

### Running with Docker

You can also build and run the application using Docker.

1.  **Build the Docker image:**
    ```bash
    docker build -t reservai-agent .
    ```

2.  **Run the Docker container:**
    Pass your environment variables from the `.env` file to the container.
    ```bash
    docker run -p 8000:8000 --env-file .env reservai-agent
    ```

## 🔌 API Endpoints

The application exposes the following endpoints:

| Method | Path                      | Description                                                  | Access        |
| :----- | :------------------------ | :----------------------------------------------------------- | :------------ |
| `GET`  | `/`                       | Root endpoint to check if the service is online.             | Public        |
| `GET`  | `/health`                 | Health check for monitoring services.                        | Public        |
| `POST` | `/whatsapp`               | The main webhook to receive messages from Twilio.            | Public        |
| `POST` | `/test-message`           | Simulate an incoming message without Twilio.                 | Dev Only      |
| `POST` | `/admin/clear-session`    | Clear the conversation history for a specific phone number.  | Dev Only      |

## 🤖 Agent Tools

The AI agent is equipped with a set of tools to perform specific actions:

-   `check_availability`: Checks available slots for a date/time and party size.
-   `create_reservation`: Creates a reservation by locking one open slot.
-   `list_reservations`: Lists reservations for a phone (active/all).
-   `cancel_reservation`: Cancels one reservation and reopens its slot.
