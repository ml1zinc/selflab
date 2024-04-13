#!/bin/bash

SERVICE="$1"

if [ $# -eq 0 ]; then
    echo "No arguments supplied"
    exit 1
fi

DEPLOY_FILE="docker-compose.${SERVICE}.yml"

if [ ! -f $DEPLOY_FILE ]; then
    echo "No deploy file(${DEPLOY_FILE}) for service: \"${SERVICE}\""

else
    echo "Start: \"${SERVICE}\" from ${DEPLOY_FILE}"
fi


docker-compose -p homelab_${SERVICE} -f ${DEPLOY_FILE} up --build -d

