del/s *.exe
del/s *.ini
del/s *.local
del/s *.identcache
del/s *.lps
del/s *.spec
rd /q /s .\lib
rd /q /s .\mcp_configs
rd /q /s .\build
rd /q /s .\dist
rd /q /s .\__pycache__
rd /q /s .\llm_common\__pycache__
call .\lingofuse\clear_.bat

