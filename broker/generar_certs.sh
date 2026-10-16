#!/bin/bash
# Genera una CA propia y un certificado de servidor valido para la IP del broker.
# Solo para Linux y macOS: en cualquier sistema se puede usar en su lugar
#   python manage.py certs [--force] [--ca]
# que hace lo mismo sin necesitar OpenSSL.
#
# Uso: ./generar_certs.sh 192.168.1.50
# Si certs/ca.key ya existe, se conserva la CA (el ESP32 no necesita reflashearse)
# y solo se regenera el certificado del servidor. Para una CA nueva, borra certs/ca.*
set -e

IP="$1"
if [ -z "$IP" ]; then
  echo "Uso: $0 <IP_del_servidor>"
  exit 1
fi

cd "$(dirname "$0")"
mkdir -p certs
cd certs

# CA (el ESP32 usara ca.crt para validar al servidor)
if [ -f ca.key ] && [ -f ca.crt ]; then
  echo "Se conserva la CA existente (ca.crt)."
else
  openssl genrsa -out ca.key 2048
  openssl req -x509 -new -nodes -key ca.key -sha256 -days 1825 \
    -subj "/CN=BusCounter CA" -out ca.crt
fi

# Certificado del servidor con la IP, 127.0.0.1 y localhost en el SAN
openssl genrsa -out server.key 2048
openssl req -new -key server.key -subj "/CN=$IP" -out server.csr
# La IP va tambien como DNS: mbedTLS 2.x (ESP32) solo compara nombres DNS del SAN
printf "subjectAltName=IP:%s,IP:127.0.0.1,DNS:localhost,DNS:%s,DNS:127.0.0.1\nextendedKeyUsage=serverAuth\n" "$IP" "$IP" > san.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 825 -sha256 -extfile san.ext

rm -f server.csr san.ext
chmod 600 ca.key server.key
echo "Listo. Archivos en ./certs (ca.crt se copia al firmware del ESP32)."
