:: Windows script to run sphinx-apidoc with appropriate options
echo off
echo -- Removing current apidoc content
del /s /q apidoc
echo -- Running sphinx-apidoc command
set SPHINX_APIDOC_OPTIONS=members,show-inheritance,noindex
sphinx-apidoc ../proteuslib "../proteuslib/*tests" -o apidoc