# Reproduction of the paper's numbers, tables and figures from the shipped result files.
#   docker build -t ua-mpc-repro .
#   docker run --rm -p 8501:8501 ua-mpc-repro             # web interface on http://localhost:8501
#   docker run --rm ua-mpc-repro python -m app.reproduce   # command line, prints the report
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLBACKEND=Agg
WORKDIR /pkg
COPY requirements-frozen.txt code/requirements.txt /tmp/req/
RUN pip install --no-cache-dir -r /tmp/req/requirements-frozen.txt -r /tmp/req/requirements.txt
COPY . /pkg
EXPOSE 8501
CMD ["python", "-m", "app.server", "--host", "0.0.0.0", "--port", "8501"]
