{% if docker.platform -%}
FROM --platform={{docker.platform}} scratch
{% else -%}
FROM scratch
{% endif -%}

COPY . /

{% if docker.ld_library_path -%}
ENV LD_LIBRARY_PATH={{docker.ld_library_path}}
{% endif -%}

{% if docker.entrypoint -%}
ENTRYPOINT {{docker.entrypoint}}
{% endif -%}
{% if docker.cmd -%}
CMD {{docker.cmd}}
{% endif -%}
