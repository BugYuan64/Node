FROM scratch
COPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY service /service
ENTRYPOINT ["/service"]
