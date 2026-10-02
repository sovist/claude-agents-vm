# Egress proxy: the only way out of the internal agent network. Allows only the hosts in the base
# allow-list (proxy/allowed-domains.txt) and the project's (project/allowed-domains.txt), both mounted
# at run time and merged by merge-filter, so changes need only a reload (allow.sh).
FROM alpine:3

RUN apk add --no-cache tinyproxy

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY --chmod=755 merge-filter /usr/local/bin/merge-filter

EXPOSE 3128
CMD ["sh", "-c", "merge-filter && exec tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf"]
