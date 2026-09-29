# iV API Architecture

## Request Flow

User
|
Frontend
|
API Route
|
iV Core Router
|
Model Provider
|
Response Processor
|
Database


## Initial Model Providers

Gemini:
- Primary reasoning

Mistral:
- Secondary reasoning
- Cost optimization


## Future Providers

- Local models
- Specialized coding models
- Research models


## Core API Responsibilities

The API layer handles:

- Authentication
- Conversation storage
- Model routing
- Memory retrieval
- Agent coordination


## First Endpoint

POST /api/chat

Input:

{
 message,
 conversation_id
}


Output:

{
 response,
 model_used,
 timestamp
}