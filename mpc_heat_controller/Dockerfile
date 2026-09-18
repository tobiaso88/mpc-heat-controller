FROM python:3.13-slim
RUN pip install --no-cache-dir websocket-client==1.8.0
WORKDIR /app
COPY app /app/app
ENV MPC_HOST=0.0.0.0 MPC_PORT=8099 MPC_DATA=/data
CMD ["python", "-m", "app.server"]
