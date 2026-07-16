# FastAPI Project 

A lightweight and scalable **FastAPI** application built with **Python 3.12+**.  

---

## ⚡ Requirements
- Python **3.12** (ensure correct version is installed)  
- `pip` (Python package manager)  
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) (for authentication and resource management)  
- `virtualenv` (recommended for isolated environments)  
- [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server) (required for database connections)  
- install postgress
  - brew install postgresql

---

## 🛠 Setup Instructions

### 1. Clone the Repository
```bash
git clone <your-repo-url>
cd fastapi-project
```

### 2. Create and Activate Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate   # For Linux / macOS
venv\Scripts\activate      # For Windows (PowerShell)
```

### 3. Install Dependencies
```bash
pip install -r requirement.txt
```

### 4. Authenticate with Azure
```bash
az login
```

### 5. Run the Application
```bash
uvicorn main:app --reload
```

The API will be available at: **http://127.0.0.1:8000**

---

## 📂 Project Structure
```
fastapi-project/
│── app/
│   ├── routes/        # API route definitions
│   ├── models/        # SQLAlchemy models
│   ├── schemas/       # Pydantic schemas
│   └── core/          # Config, dependencies, utilities
│
│── requirements.txt   # Python dependencies
│── main.py            # FastAPI entry point
└── README.md          # Project documentation
```
