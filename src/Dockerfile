FROM python:3.13.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/src
COPY src/requirements.txt .
RUN pip install -r requirements.txt
COPY src/ .
COPY fixtures.json /app/fixtures.json
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput -v0
RUN useradd --uid 10001 --create-home portal && mkdir -p /data && chown portal /data
USER portal
EXPOSE 8080
ENTRYPOINT ["./entrypoint.sh"]
