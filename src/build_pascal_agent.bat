lazbuild.exe -B ./pascal_agent_service.lpi
lazbuild.exe -B ./pascal_agent_api.lpi
lazbuild.exe -B ./CreateHealthCheck/HealthCheck.lpi
lazbuild.exe -B ./llm_tool_v3.lpi

echo 所有项目编译完成。
timeout /t 5 /nobreak >nul
