# Include inside ONLY the CompeTIDOR HTTPS server block.
location ^~ /recovery/ {
    proxy_pass http://127.0.0.1:8771;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_connect_timeout 3s;
    proxy_read_timeout 10s;
    client_max_body_size 1k;
}
