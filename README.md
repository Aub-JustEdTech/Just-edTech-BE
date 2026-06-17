# 🤖 Just-EdTech - Retrieval-Augmented Generation API

A modern, scalable FastAPI-based backend for Retrieval-Augmented Generation (RAG) with modular architecture, robust authentication, and intelligent document processing.


---

## 📋 Table of Contents
- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Setup Option 1: Docker](#-setup-option-1-docker-compose-recommended)
- [Setup Option 2: Local Development](#-setup-option-2-local-development)
- [Configuration](#-configuration-details)
- [Database Management](#-database-management)
- [Services Overview](#-services-overview)
- [Authentication & Users](#-authentication--users)
- [RAG Features](#-rag-features)
- [Development](#-development)
- [Troubleshooting](#-troubleshooting)
- [Quick Reference](#-quick-reference-commands)

## ✨ Features

- 🤖 **Retrieval-Augmented Generation** - Intelligent document querying with semantic search
- 📄 **Document Processing** - Automatic text chunking and embedding generation
- 🔍 **Semantic Search** - Similarity-based document retrieval using OpenAI embeddings
- 💬 **Advanced Chat Features** - Multi-threaded conversations with context management
- 🏷️ **Auto-Generated Titles** - Smart conversation titles from first message
- 📚 **Citation Support** - Automatic source citations with document references
- 🔄 **Context Window Management** - Intelligent conversation history for LLM context
- 📄 **Message Pagination** - Efficient handling of long conversation histories
- 🔐 **JWT-based Authentication** - Secure user registration and login
- 🛡️ **Password Security** - BCrypt hashing with strong password validation
- 🗄️ **Database Integration** - PostgreSQL with SQLAlchemy ORM
- 🔄 **Database Migrations** - Alembic for version-controlled schema changes
- 📝 **API Documentation** - Auto-generated OpenAPI/Swagger docs
- 🏗️ **Clean Architecture** - Separation of concerns with CRUD, schemas, and endpoints
- ✅ **Type Safety** - Full Pydantic validation and type hints
- 🚀 **Fast & Modern** - Built with FastAPI for high performance
- 🧠 **Modular Design** - Easy to extend with new embedding models or LLMs
- 📊 **Token Tracking & Analytics** - Comprehensive monitoring of LLM usage and costs
- 💰 **Automated Billing System** - Daily token aggregation and monthly billing reports per tenant per model
- 💵 **Cost Calculation** - Automatic cost calculation based on configurable model pricing
- 📈 **Billing Reports** - Detailed monthly and yearly billing summaries with per-model breakdown

## 🛠 Tech Stack

- **Framework**: FastAPI 0.104.1
- **Database**: PostgreSQL
- **ORM**: SQLAlchemy 2.0.23
- **Migrations**: Alembic 1.12.1
- **Authentication**: JWT (PyJWT)
- **Password Hashing**: BCrypt
- **Validation**: Pydantic
- **Embeddings**: OpenAI
- **Text Processing**: LangChain
- **Machine Learning**: NumPy, Scikit-learn
- **Environment**: Python 3.12.11
- **Package Manager**: Poetry

## 📁 Project Structure

```
Just-EdTech-BE/
├── add_user_docker.py
├── alembic/                    # Database migrations
│   ├── env.py                 # Alembic environment
│   ├── script.py.mako         # Migration template
│   └── versions/              # Migration files
│       └── 0ccc8288ae0a_sync_models_to_erd.py
├── alembic.ini               # Alembic configuration
├── app/
│   ├── __init__.py
│   ├── api/                   # API layer
│   │   ├── __init__.py
│   │   ├── api.py             # Main API router
│   │   └── endpoints/         # API endpoints
│   │       ├── __init__.py
│   │       ├── auth.py        # Authentication endpoints
│   │       ├── conversations.py # Chat management
│   │       ├── documents.py   # Document management
│   │       ├── rag.py         # RAG query endpoints
│   │       └── rag_management/ # RAG management endpoints
│   ├── core/                  # Core configuration
│   │   ├── __init__.py
│   │   └── config.py          # Environment settings
│   ├── crud/                  # Database operations
│   │   ├── __init__.py
│   │   ├── conversations.py   # Conversation CRUD operations
│   │   ├── documents.py       # Document CRUD operations
│   │   └── users.py           # User CRUD operations
│   ├── db/                    # Database setup
│   │   ├── __init__.py
│   │   └── connector.py       # Database connection
│   ├── main.py                # FastAPI application
│   ├── models/                # SQLAlchemy models
│   │   ├── __init__.py
│   │   ├── api_keys.py
│   │   ├── associations.py
│   │   ├── base.py            # Base model class
│   │   ├── billing.py
│   │   ├── conversations.py   # Conversation & Message models
│   │   ├── documents.py       # Document model
│   │   ├── feedback.py
│   │   ├── invitations.py
│   │   ├── monitoring.py
│   │   ├── roles.py
│   │   ├── tenant_configs.py
│   │   ├── tenants.py
│   │   └── users.py           # User model
│   ├── schemas/               # Pydantic schemas
│   │   ├── __init__.py
│   │   ├── conversations.py   # Conversation schemas
│   │   ├── documents.py       # Document schemas
│   │   ├── rag.py             # RAG-specific schemas
│   │   └── users.py           # User schemas
│   └── utils/                 # Utility functions
│       ├── __init__.py
│       ├── auth.py            # JWT utilities
│       ├── dependencies.py    # FastAPI dependencies
│       └── rag.py             # RAG processing utilities
├── build.sh
├── Dockerfile                # Docker container definition
├── docker-compose.yml        # Multi-service Docker setup
├── init.sql                 # Database initialization
├── poetry.lock               # Locked dependencies
├── POSTMAN_GUIDE.md
├── pyproject.toml            # Poetry dependencies
├── quality_check.sh
├── quality_checks/
│   └── run_quality_checks.py
├── README.md                # This file
├── scripts/
│   ├── __init__.py
│   ├── clear_vectors.py
│   ├── seed_roles.py
│   └── setup_model_pricing.py
├── tests/                     # Test files
│   ├── __init__.py
│   └── test_main.py           # Basic tests
├── vector_db/                 # Vector database storage
└── docker/ (optional)         # If you add extra docker assets
```

## 📋 Prerequisites

**For Docker Setup (Recommended):**
- Docker & Docker Compose installed
- OpenAI API Key ([Get one here](https://platform.openai.com/))

**For Local Setup:**
- Python 3.12.11
- PostgreSQL 15+
- Redis 7+
- Poetry (Python package manager)
- OpenAI API Key

---

## 🐳 Setup Option 1: Docker Compose 

The fastest way to get everything running with all dependencies included.

### Step 1: Clone the Repository

```bash
git clone <your-repository-url>
cd Just-EdTech-BE
```

### Step 2: Create Environment File

Create a `.env` file in the project root. Refer to `.env.example` for all variables and copy it:

```bash
cp .env.example .env
```

### Step 3: Start All Services

```bash
# Start all services (PostgreSQL, Redis, API, Celery Worker, Flower)
docker-compose up -d --build

# View logs
docker-compose logs -f api

# View all services status
docker-compose ps
```

### Step 4: Verify Setup

**Access the services:**
- 🌐 **API Documentation**: http://localhost:8000/docs
- 🌸 **Flower (Celery Monitoring)**: http://localhost:5555
- 💾 **PostgreSQL**: localhost:5432
- 🔴 **Redis**: localhost:6379

**The application is ready!** Database migrations run automatically on startup.

### Managing Docker Services

```bash
# Stop all services
docker-compose down

# Stop and remove all data (fresh start)
docker-compose down -v

# View logs for specific service
docker-compose logs -f api
docker-compose logs -f celery-worker
docker-compose logs -f postgres

# Restart a specific service
docker-compose restart api

# Scale celery workers (for heavy processing)
docker-compose up -d --scale celery-worker=4
```

---

## 💻 Setup Option 2: Local Development

For development without Docker or when you need more control.

### Step 1: Clone & Setup Environment

```bash
# Clone repository
git clone <your-repository-url>
cd Just-EdTech-BE

# Install Poetry (if not installed)
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install

# Activate virtual environment
poetry shell
```

### Step 2: Setup PostgreSQL Database

```bash
# Login to PostgreSQL
psql -U postgres

# Create database
CREATE DATABASE "just_edtech";

# Exit psql
\q
```

### Step 3: Setup Redis

```bash
# Install Redis (Ubuntu/Debian)
sudo apt-get install redis-server

# Start Redis
sudo systemctl start redis-server

# Verify Redis is running
redis-cli ping  # Should return: PONG
```



### Step 5: Run Database Migrations

```bash
# Apply all migrations
poetry run alembic upgrade head

# Verify migration
poetry run alembic current
```

### Step 6: Start the Application

**Terminal 1 - Start FastAPI Server:**
```bash
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2 - Start Celery Worker (for background tasks):**
```bash
poetry run celery -A app.celery_app worker --loglevel=info --concurrency=10
```

**Terminal 3 - Start Celery Beat (for scheduled tasks like daily aggregation):**
```bash
poetry run celery -A app.celery_app beat --loglevel=info
```

**Terminal 4 - Start Flower (Optional - Celery monitoring):**
```bash
poetry run celery -A app.celery_app flower --port=5555
```

### Step 7: Verify Local Setup

- 🌐 **API Documentation**: http://localhost:8000/docs
- 🌸 **Flower Dashboard**: http://localhost:5555

---

## 🔧 Configuration Details

Refer to `.env.example` for all required and optional environment variables.

Quick start:
- Copy `.env.example` to `.env`
- For Docker, other values default from `docker-compose.yml`
- For local development, adjust PostgreSQL/Redis/S3 values as needed

Example:
```bash
cp .env.example .env
```

---

## 🗄️ Database Management

### Migrations

```bash
# Create new migration
poetry run alembic revision --autogenerate -m "Description"

# Apply migrations
poetry run alembic upgrade head

# Rollback migration
poetry run alembic downgrade -1

# View migration history
poetry run alembic history

# Check current version
poetry run alembic current
```

### Using Docker

```bash
# Run migrations in Docker
docker-compose exec api alembic upgrade head

# Create new migration in Docker
docker-compose exec api alembic revision --autogenerate -m "Description"
```

---

## 📚 Services Overview

### Main Services

| Service | Port | Description |
|---------|------|-------------|
| **FastAPI API** | 8000 | Main application server |
| **PostgreSQL** | 5432 | Primary database |
| **Redis** | 6379 | Cache & message broker |
| **Celery Worker** | - | Background task processor |
| **Celery Beat** | - | Periodic task scheduler (cron jobs) |
| **Flower** | 5555 | Celery monitoring dashboard |

### API Endpoints

Once running, access the interactive API documentation:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## 📊 Token Tracking & Analytics

### Overview
- **Automatic Token Tracking**: All LLM responses are automatically tracked with input/output token counts
- **Cost Monitoring**: Track token usage per user, tenant, and model for cost analysis
- **Daily Aggregation**: Automated daily summaries of token usage per tenant and model
- **Analytics API**: RESTful endpoints for usage statistics and reporting

### Real-Time Analytics Endpoints
```bash
# User token usage
GET /analytics/users/{user_id}/token-usage?tenant_id={tenant_id}

# Tenant-wide usage  
GET /analytics/tenants/{tenant_id}/token-usage

# Model-specific breakdown
GET /analytics/token-usage/by-model?tenant_id={tenant_id}
```

### Daily Token Usage Aggregation

The system automatically aggregates token usage data daily at 2:00 AM UTC, providing efficient historical tracking and reporting.

**Key Features:**
- **Automated Daily Aggregation**: Runs every day at 2 AM UTC via Celery Beat
- **Per-Tenant Tracking**: Separate usage statistics for each tenant
- **Per-Model Breakdown**: Track usage by AI model (GPT-4, GPT-3.5, etc.)
- **Input/Output Separation**: Separate tracking of input and output tokens
- **Message Count**: Track number of messages processed per day

**Daily Usage API Endpoints:**
```bash
# Get daily usage for date range
GET /token-usage/daily?start_date=2025-01-01&end_date=2025-01-31&model_name=gpt-4

# Get usage summary with totals
GET /token-usage/summary?start_date=2025-01-01&end_date=2025-01-31

# Get current month usage
GET /token-usage/current-month

# Get last 30 days usage
GET /token-usage/last-30-days

# Manually trigger aggregation for a specific date
POST /token-usage/aggregate?target_date=2025-01-15

# Backfill historical data
POST /token-usage/backfill?start_date=2025-01-01&end_date=2025-01-31
```

**Response Example:**
```json
{
  "by_model": {
    "gpt-4": {
      "input_tokens": 15000,
      "output_tokens": 8500,
      "total_tokens": 23500,
      "message_count": 45
    },
    "gpt-3.5-turbo": {
      "input_tokens": 8000,
      "output_tokens": 4200,
      "total_tokens": 12200,
      "message_count": 30
    }
  },
  "totals": {
    "input_tokens": 23000,
    "output_tokens": 12700,
    "total_tokens": 35700,
    "message_count": 75
  },
  "date_range": {
    "start_date": "2025-01-01",
    "end_date": "2025-01-31"
  }
}
```

### Background Task Management

**Starting Celery Beat (for scheduled tasks):**
```bash
# Docker
docker-compose up -d celery-beat

# Local development
poetry run celery -A app.celery_app beat --loglevel=info
```

**Note**: The daily aggregation task runs automatically. You can also trigger it manually via the API for immediate processing or backfilling historical data.

## 💰 Token Billing & Cost Management

### Overview

The billing system provides comprehensive cost tracking and monthly billing reports based on token usage and configurable model pricing.

**Key Features:**
- **Automated Cost Calculation**: Daily cost calculation based on model pricing
- **Per-Model Pricing**: Configure different prices for input, output, and cache tokens
- **Monthly Billing Reports**: Automated monthly aggregation on the 1st of each month
- **Detailed Breakdowns**: Cost analysis by tenant, model, and billing period
- **Yearly Summaries**: Annual billing reports with monthly breakdown

### Setting Up Model Pricing

Before costs can be calculated, configure pricing for each model in your `llm_models` table:

```sql
-- Example: Set pricing for GPT-4 (prices per 1M tokens in USD)
UPDATE llm_models 
SET 
  input_token_price = 30.00,   -- $30 per 1M input tokens
  output_token_price = 60.00,  -- $60 per 1M output tokens
  cache_token_price = 15.00    -- $15 per 1M cache tokens
WHERE name = 'gpt-4' AND tenant_id = 1;

-- Example: Set pricing for GPT-3.5-turbo
UPDATE llm_models 
SET 
  input_token_price = 0.50,    -- $0.50 per 1M input tokens
  output_token_price = 1.50,   -- $1.50 per 1M output tokens
  cache_token_price = 0.25     -- $0.25 per 1M cache tokens
WHERE name = 'gpt-3.5-turbo' AND tenant_id = 1;
```

### Automated Billing Tasks

#### Daily Token Aggregation with Cost Calculation
**Schedule:** Daily at 2:00 AM UTC  
**Task:** `aggregate_daily_token_usage`

Aggregates token usage and calculates costs using configured model pricing.

#### Monthly Billing Aggregation
**Schedule:** 1st of each month at 3:00 AM UTC  
**Task:** `aggregate_monthly_billing`

Generates monthly billing reports by aggregating daily usage data.

### Monthly Billing API Endpoints

```bash
# Get monthly billing records
GET /billing/monthly-billing/{tenant_id}?year=2024&month=10&model_name=gpt-4

# Get monthly billing summary with cost breakdown
GET /billing/monthly-billing/{tenant_id}/summary?year=2024&month=10

# Get yearly billing summary
GET /billing/yearly-billing/{tenant_id}?year=2024

# Manually trigger monthly billing aggregation
POST /billing/monthly-billing/aggregate
{
  "year": 2024,    // optional, defaults to previous month
  "month": 10      // optional, defaults to previous month
}

# Check aggregation task status
GET /billing/monthly-billing/task/{task_id}
```

### Monthly Billing Response Example

```json
{
  "by_model": {
    "gpt-4": {
      "input_tokens": 5000000,
      "output_tokens": 2500000,
      "cache_tokens": 500000,
      "total_tokens": 8000000,
      "message_count": 1250,
      "input_cost": 150.00,
      "output_cost": 150.00,
      "cache_cost": 7.50,
      "total_cost": 307.50
    },
    "gpt-3.5-turbo": {
      "input_tokens": 10000000,
      "output_tokens": 5000000,
      "cache_tokens": 1000000,
      "total_tokens": 16000000,
      "message_count": 3000,
      "input_cost": 5.00,
      "output_cost": 7.50,
      "cache_cost": 0.25,
      "total_cost": 12.75
    }
  },
  "totals": {
    "input_tokens": 15000000,
    "output_tokens": 7500000,
    "cache_tokens": 1500000,
    "total_tokens": 24000000,
    "message_count": 4250,
    "input_cost": 155.00,
    "output_cost": 157.50,
    "cache_cost": 7.75,
    "total_cost": 320.25
  },
  "billing_period": {
    "year": 2024,
    "month": 10
  }
}
```

### Yearly Billing Response Example

```json
{
  "tenant_id": 1,
  "year": 2024,
  "monthly_breakdown": {
    "1": {"month": 1, "total_tokens": 20000000, "total_cost": 450.00},
    "2": {"month": 2, "total_tokens": 22000000, "total_cost": 480.50},
    "3": {"month": 3, "total_tokens": 18000000, "total_cost": 390.75}
  },
  "yearly_totals": {
    "total_tokens": 60000000,
    "total_cost": 1321.25
  }
}
```

### Cost Calculation Formula

```
Input Cost = (Total Input Tokens / 1,000,000) × Input Token Price
Output Cost = (Total Output Tokens / 1,000,000) × Output Token Price
Cache Cost = (Total Cache Tokens / 1,000,000) × Cache Token Price
Total Cost = Input Cost + Output Cost + Cache Cost
```

### Manual Billing Operations

```bash
# Generate bill for specific month (via API)
curl -X POST http://localhost:8000/api/billing/monthly-billing/aggregate \
  -H "Content-Type: application/json" \
  -d '{"year": 2024, "month": 10}'

# Generate bill for previous month automatically
curl -X POST http://localhost:8000/api/billing/monthly-billing/aggregate \
  -H "Content-Type: application/json" \
  -d '{}'
```

For comprehensive documentation, see: [docs/TOKEN_BILLING_SYSTEM.md](docs/TOKEN_BILLING_SYSTEM.md)

### Database Migration Required
```bash
# Apply token tracking schema changes
alembic upgrade head
```

## 🔐 Authentication & Users

### Creating Your First User

**Option 1: Using the Seed Script**
```bash
# Run the roles seeding script with default tenant and admin
python scripts/seed_roles.py --with-defaults
```

**Option 2: Using the API**
1. Go to http://localhost:8000/docs
2. Use the `/api/v1/auth/register` endpoint
3. Create a new user with your credentials

### Authentication Flow

### Password Requirements

- Minimum 8 characters
- At least one uppercase letter
- At least one lowercase letter  
- At least one digit

## 🤖 RAG Features

### Document Processing

1. **Upload Documents**: Users can upload text documents through the API
2. **Automatic Chunking**: Documents are automatically split into manageable chunks
3. **Embedding Generation**: Text chunks are converted to vector embeddings using OpenAI embeddings
4. **Vector Storage**: Embeddings are stored for fast similarity search

### Query Processing

1. **Semantic Search**: User queries are matched against document embeddings
2. **Context Retrieval**: Most relevant document chunks are retrieved
3. **Response Generation**: Retrieved context is used to generate informed responses
4. **Conversation Tracking**: Chat history is maintained across sessions

### Supported Features

- **Multiple Document Formats**: Text-based documents
- **Configurable Chunking**: Adjustable chunk size and overlap
- **Similarity Scoring**: Cosine similarity for document retrieval
- **Background Processing**: Asynchronous document processing
- **Status Tracking**: Monitor document processing status

## 💬 Chat Features

### Multiple Conversation Threads

- **Conversation Management**: Users can maintain multiple separate conversation threads
- **Auto-Generated Titles**: Conversation titles are automatically generated from the first message (first 7 words or 50 characters)
- **Conversation List**: Paginated list of conversations with last message preview
- **Context Isolation**: Each conversation maintains its own context and history

### Advanced Message Handling

- **Message Pagination**: Efficient handling of long conversation histories with configurable page sizes
- **Role-Based Messages**: Support for user, assistant, and system message types
- **Timestamps**: All messages include creation and update timestamps
- **Message History**: Complete conversation history with proper ordering

### Citation Support

- **Automatic Citations**: Bot responses include citations to source documents
- **Document References**: Citations include document title, URL, and relevant text snippets
- **Position Tracking**: Citations are ordered by relevance and position
- **Snippet Extraction**: Relevant text snippets are extracted and included in citations

### Context Window Management

- **Intelligent Context**: Last N messages are included in LLM context (configurable, default: 10 messages)
- **Context Formatting**: Conversation history is properly formatted for LLM consumption
- **Context Length Management**: Automatic truncation to stay within token limits
- **Memory Efficiency**: Only relevant recent messages are loaded for context

### Configuration Options

```python
# Chat-specific settings in app/core/config.py
CONTEXT_WINDOW_SIZE = 10                    # Number of messages for LLM context
CONVERSATION_TITLE_MAX_LENGTH = 50          # Max characters for auto-generated titles
CONVERSATION_TITLE_WORD_COUNT = 7           # Max words for titles
MESSAGE_PAGINATION_DEFAULT_LIMIT = 50       # Default messages per page
CONVERSATION_PAGINATION_DEFAULT_LIMIT = 20  # Default conversations per page
```

### API Endpoints

- `GET /conversations` - List user's conversations with pagination
- `GET /conversations/{id}` - Get conversation details
- `POST /conversations/{id}/messages` - Send message (auto-create conversation if id=0)
- `GET /conversations/{id}/messages` - Get paginated message history

## 👨‍💻 Development

### Adding New Features

1. **Models**: Add new SQLAlchemy models in `app/models/`
2. **Schemas**: Create Pydantic schemas in `app/schemas/`
3. **CRUD**: Implement database operations in `app/crud/`
4. **Endpoints**: Add API endpoints in `app/api/endpoints/`
5. **Migrations**: Generate migrations with `alembic revision --autogenerate`

### Code Quality

The project uses:
- **Ruff**: For linting and formatting
- **Black**: Code formatting
- **isort**: Import sorting

Run code quality checks:
```bash
# Check code with Ruff (only checks app directory)
poetry run ruff check app/

# Format code with Ruff (only formats app directory)
poetry run ruff format app/

# Check formatting with Black (only checks app directory)
poetry run black . --check

# Format code with Black (only formats app directory)
poetry run black .
```

### Database Migrations

```bash
# Create new migration
alembic revision --autogenerate -m "Description of changes"

# Apply migrations
alembic upgrade head

# Rollback migration
alembic downgrade -1

# View migration history
alembic history
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes
4. Run tests and linting
5. Commit your changes: `git commit -m "Add your feature"`
6. Push to the branch: `git push origin feature/your-feature`
7. Submit a pull request

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Troubleshooting


## 🔗 Quick Reference Commands

### Docker Commands
```bash
# Start everything
docker-compose up -d --build

# Stop everything
docker-compose down

# Fresh start (deletes data!)
docker-compose down -v && docker-compose up -d --build

# View logs
docker-compose logs -f [service_name]

# Access container shell
docker-compose exec api bash
docker-compose exec postgres psql -U postgres

# Run migrations
docker-compose exec api alembic upgrade head

# Scale workers
docker-compose up -d --scale celery-worker=4
```

### Local Development Commands
```bash
# Install & setup
poetry install
poetry shell

# Run migrations
poetry run alembic upgrade head

# Start services
poetry run uvicorn app.main:app --reload
poetry run celery -A app.celery_app worker --loglevel=info
poetry run celery -A app.celery_app beat --loglevel=info
poetry run celery -A app.celery_app flower

# Database
poetry run alembic revision --autogenerate -m "Description"
poetry run alembic upgrade head
poetry run alembic downgrade -1

# Code quality
poetry run ruff check .
poetry run ruff format .
poetry run black .
```

## 📞 Support

For support and questions:
- Create an issue in the repository
- Check the API documentation at `/docs`
- Review the troubleshooting section above

---

**Built with ❤️ using FastAPI, modern Python practices, and cutting-edge RAG technology**