FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
ENV HOST=0.0.0.0 PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST} --port ${PORT}"]
