# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies (if any are needed for build)
# For example, build-essential might be needed for some python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Ensure the uploads directory exists
RUN mkdir -p uploads

# Expose the port the FastAPI app runs on
EXPOSE 8000

# Command to run the application
# Environment variables will be injected at runtime (e.g., via docker-compose or --env-file)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
