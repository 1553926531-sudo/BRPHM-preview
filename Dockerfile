FROM rul-cpu:latest

WORKDIR /opt/BRPHM-preview
COPY . .

RUN mkdir -p /mnt/data/BRPHM \
    && ln -s /opt/BRPHM-preview /mnt/data/BRPHM/rul-space

ENV PYTHONUNBUFFERED=1
EXPOSE 8501
ENTRYPOINT ["python", "-m", "brphm"]
CMD ["serve", "--address", "0.0.0.0", "--port", "8501"]
