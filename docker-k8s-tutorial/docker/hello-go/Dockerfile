FROM golang:1.21.3 AS dev
COPY . /root/hello/
RUN go build -o /hello /root/hello/hello.go

FROM scratch
COPY --from=dev /hello .
ENTRYPOINT [ "/hello" ]

