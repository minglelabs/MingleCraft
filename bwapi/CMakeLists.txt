cmake_minimum_required(VERSION 3.24)
project(JevCraftBridge VERSION 0.1.0 LANGUAGES CXX)

if(NOT WIN32 OR NOT MSVC)
  message(FATAL_ERROR "The BWAPI 4.4.0 bridge requires Windows, MSVC and a Win32 build.")
endif()
if(NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
  message(FATAL_ERROR "BWAPI runs inside 32-bit StarCraft. Configure with -A Win32.")
endif()
set(BWAPI_ROOT "" CACHE PATH "Extracted BWAPI 4.4.0 SDK root (contains include and lib)")
find_path(BWAPI_INCLUDE_DIR BWAPI.h PATHS "${BWAPI_ROOT}/include" REQUIRED NO_DEFAULT_PATH)
find_library(BWAPI_LIBRARY NAMES BWAPI BWAPILIB PATHS "${BWAPI_ROOT}/lib" NO_DEFAULT_PATH)
if(NOT BWAPI_LIBRARY)
  # The official 4.4.0 archive ships BWAPILIB sources rather than a prebuilt .lib.
  file(GLOB BWAPI_SOURCES CONFIGURE_DEPENDS "${BWAPI_ROOT}/BWAPILIB/Source/*.cpp")
  if(NOT BWAPI_SOURCES OR NOT EXISTS "${BWAPI_ROOT}/BWAPILIB/UnitCommand.cpp")
    message(FATAL_ERROR "BWAPI_ROOT must contain lib/BWAPI.lib or the official BWAPILIB sources.")
  endif()
  add_library(bwapi_sdk STATIC ${BWAPI_SOURCES} "${BWAPI_ROOT}/BWAPILIB/UnitCommand.cpp")
  target_include_directories(bwapi_sdk PUBLIC "${BWAPI_INCLUDE_DIR}")
  target_compile_definitions(bwapi_sdk PRIVATE NOMINMAX _CRT_SECURE_NO_WARNINGS)
  set_property(TARGET bwapi_sdk PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
  set(BWAPI_LIBRARY bwapi_sdk)
endif()

include(FetchContent)
FetchContent_Declare(json
  URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
  URL_HASH SHA256=d6c65aca6b1ed68e7a182f4757257b107ae403032760ed6ef121c9d55e81757d
)
FetchContent_MakeAvailable(json)
add_library(JevCraft SHARED src/Bridge.cpp src/Http.cpp)
target_compile_features(JevCraft PRIVATE cxx_std_17)
target_include_directories(JevCraft PRIVATE "${BWAPI_INCLUDE_DIR}")
target_link_libraries(JevCraft PRIVATE "${BWAPI_LIBRARY}" nlohmann_json::nlohmann_json winhttp)
target_compile_definitions(JevCraft PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
# Match the BWAPI SDK release library's /MD runtime and Release configuration.
set_property(TARGET JevCraft PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
target_compile_options(JevCraft PRIVATE /W4 /EHsc)
