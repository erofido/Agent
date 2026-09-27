FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent ./agent
ENV DB_PATH=/data/agent.db
CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
