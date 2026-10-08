FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && pip uninstall -y opencv-python \
    && pip install --no-cache-dir --force-reinstall opencv-python-headless

COPY component /app/component/ 
COPY app.py /app/

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]
