# ShopBot agent API for a Hugging Face Space (SDK: Docker). Spaces route traffic to port 7860.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860
WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Spaces run the container as a non-root user
RUN useradd -m -u 1000 user
USER user

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT}"]
