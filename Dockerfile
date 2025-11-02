# Gunakan image Python resmi
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Salin semua file ke container
COPY . /app

# Install dependensi
RUN pip install --no-cache-dir \
    requests \
    pandas \
    numpy \
    ta \
    schedule \
    futures

# Set timezone (opsional)
ENV TZ=Asia/Jakarta

# Jalankan bot
CMD ["python", "main.py"]