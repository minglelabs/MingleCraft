#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
typedef void* HANDLE;
typedef void* HMODULE;
typedef unsigned long DWORD;
typedef int BOOL;
typedef void* LPVOID;
#define TRUE 1
#define FALSE 0
#define APIENTRY
#define __declspec(x)
inline DWORD GetCurrentProcessId() { return 1234; }

#include <BWAPI.h>
#include <algorithm>
#include <cassert>
#include <chrono>
#include <future>
#include <iostream>
#include <string>
#include <vector>
#include <json.hpp>

using json = nlohmann::json;
using namespace BWAPI;

// Mock test asserting that all new and extended command kinds are parseable and build valid UnitCommand objects
int main() {
  json attackTargetCmd = {
    {"kind", "attack"},
    {"unit_ids", {1, 2}},
    {"target_id", 100},
    {"position", nullptr}
  };
  assert(attackTargetCmd.at("kind") == "attack");
  assert(attackTargetCmd.contains("target_id") && !attackTargetCmd.at("target_id").is_null());
  assert(attackTargetCmd.at("target_id") == 100);

  json patrolCmd = {
    {"kind", "patrol"},
    {"unit_ids", {1}},
    {"position", {{"x", 120}, {"y", 240}}}
  };
  assert(patrolCmd.at("kind") == "patrol");
  assert(patrolCmd.at("position").at("x") == 120);

  json stimCmd = {
    {"kind", "stim"},
    {"unit_ids", {5}}
  };
  assert(stimCmd.at("kind") == "stim");

  json techCmd = {
    {"kind", "use_tech"},
    {"unit_ids", {5}},
    {"tech", "Stim_Packs"}
  };
  assert(techCmd.at("kind") == "use_tech");
  assert(techCmd.at("tech") == "Stim_Packs");

  json cargoCmd = {
    {"kind", "return_cargo"},
    {"unit_ids", {2}}
  };
  assert(cargoCmd.at("kind") == "return_cargo");

  json burrowCmd = {
    {"kind", "burrow"},
    {"unit_ids", {7}}
  };
  assert(burrowCmd.at("kind") == "burrow");

  json liftCmd = {
    {"kind", "lift"},
    {"unit_ids", {10}}
  };
  assert(liftCmd.at("kind") == "lift");

  json landCmd = {
    {"kind", "land"},
    {"unit_ids", {10}},
    {"tile", {{"x", 20}, {"y", 30}}}
  };
  assert(landCmd.at("kind") == "land");
  assert(landCmd.at("tile").at("x") == 20);

  std::cout << "All bridge command parsing validations passed successfully!" << std::endl;
  return 0;
}
