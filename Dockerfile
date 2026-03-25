FROM python:3.11-slim

WORKDIR /app

COPY Pipfile ./
RUN pip install --no-cache-dir requests psycopg2-binary boto3

COPY . .

CMD ["python", "scrape.py"]
