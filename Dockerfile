FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY parser.py pdfplumber_app.py /app/

CMD ["streamlit", "run", "pdfplumber_app.py", "--server.address=0.0.0.0"]
