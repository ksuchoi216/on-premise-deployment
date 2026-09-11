FROM python:3.12-slim

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Install dependencies first for better caching
COPY app/requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Copy application code
COPY ./app /app/app

# Start the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
