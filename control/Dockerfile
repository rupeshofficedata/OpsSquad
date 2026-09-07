FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 6001

CMD ["gunicorn", "-b", "0.0.0.0:6001", "app:create_app()"]
