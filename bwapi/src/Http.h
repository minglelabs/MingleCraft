#pragma once
#include <nlohmann/json.hpp>

// No BWAPI calls are allowed on this worker-thread transport.
nlohmann::json postLocal(const wchar_t* path, const nlohmann::json& payload);

