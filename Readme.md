# RTFM Agent 🤖

> AI-powered AWS documentation assistant that provides working code examples with IAM policies.

## 🚀 Features

- **RAG Pipeline**: Retrieves relevant AWS/Boto3 documentation
- **Code-First**: Returns production-ready Python code
- **IAM Aware**: Generates minimal required IAM policies
- **Conversation Memory**: Maintains context across queries
- **Source Citation**: Shows where answers come from
- **Multi-Turn**: Handles follow-up questions intelligently

## 🛠️ Tech Stack

- **Backend**: FastAPI, Python 3.11
- **LLM**: Ollama/Llama3 or OpenAI GPT
- **Embeddings**: Sentence-Transformers
- **Vector DB**: ChromaDB
- **Frontend**: Streamlit (coming soon)
- **Deployment**: Docker, GCP Cloud Run

## 📦 Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/rtfm-agent.git
cd rtfm-agent

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements/dev.txt

# Setup pre-commit hooks
pre-commit install

# Run the application
python run.py