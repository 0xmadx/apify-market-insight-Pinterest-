# No browser image. This actor makes HTTP requests with a session it is handed,
# so it runs on the plain Python base — roughly a quarter of the compute units of
# a Playwright image, and nothing to keep patched.
FROM apify/actor-python:3.12

COPY requirements.txt ./
RUN echo "Python: $(python --version)" \
 && pip install --no-cache-dir -r requirements.txt \
 && pip freeze

COPY . ./

CMD ["python", "-m", "src.main"]
