#include <BWAPI.h>
#include <Windows.h>
#include <algorithm>
#include <chrono>
#include <future>
#include <string>
#include <vector>
#include "Http.h"

using json = nlohmann::json;
using namespace BWAPI;

namespace {
json position(Position p) { return {{"x", p.x}, {"y", p.y}}; }
json tilePosition(TilePosition p) { return {{"x", p.x}, {"y", p.y}}; }

class Bridge final : public AIModule {
  std::future<json> pending;
  static UnitType findUnitType(const std::string& name) {
    for (auto type : UnitTypes::allUnitTypes()) {
      if (type.getName() == name) return type;
    }
    return UnitTypes::Unknown;
  }
  std::string match;
  bool active = false;
  int lastSent = -6;
  int retryFrame = 0;
  int lastAppliedObservation = -1;
  int attempted = 0, accepted = 0, effective = 0;
  json receipts = json::array();
  std::vector<int> destroyed;

  json snapshot() {
    auto self = Broodwar->self();
    auto enemy = Broodwar->enemy();
    auto homeTile = self->getStartLocation();
    auto home = Position(homeTile) + Position(64, 48);
    json own = json::array(), enemies = json::array(), minerals = json::array(), locations = json::array();
    std::vector<Unit> patches;
    for (auto u : Broodwar->getMinerals()) {
      if (u->exists() && u->isVisible() && u->getPosition().isValid()) {
        patches.push_back(u);
        minerals.push_back({{"id", u->getID()}, {"position", position(u->getPosition())}});
      }
    }
    std::vector<std::pair<UnitType, TilePosition>> sites;
    for (auto type : UnitTypes::allUnitTypes()) {
      if (type.getRace() != self->getRace() || !type.isBuilding() || type.isSpecialBuilding()) continue;
      auto tile = Broodwar->getBuildLocation(type, homeTile, 24);
      if (tile.isValid()) sites.push_back({type, tile});
    }
    for (auto u : self->getUnits()) {
      if (!u->exists() || !u->getPosition().isValid()) continue;
      json trains = json::array(), gathers = json::array(), builds = json::array();
      if (u->isCompleted() && !u->isTraining()) {
        for (auto type : UnitTypes::allUnitTypes()) {
          if (type.getRace() != self->getRace()) continue;
          if (u->canTrain(type)) trains.push_back(type.getName());
        }
      }
      if (u->getType().isWorker() && !u->isConstructing()) {
        for (auto patch : patches) if (u->canGather(patch)) gathers.push_back(patch->getID());
        for (auto g : Broodwar->getAllUnits()) {
          if (g->exists() && g->getType().isRefinery() && g->getPlayer() == self && g->isCompleted() && u->canGather(g)) {
            gathers.push_back(g->getID());
          }
        }
        for (auto site : sites) if (u->canBuild(site.first, site.second))
          builds.push_back({{"unit_type", site.first.getName()}, {"tile", tilePosition(site.second)}});
      }
      own.push_back({
        {"id", u->getID()}, {"type", u->getType().getName()}, {"position", position(u->getPosition())},
        {"hit_points", u->getHitPoints()}, {"completed", u->isCompleted()}, {"idle", u->isIdle()},
        {"training", u->isTraining()}, {"constructing", u->isConstructing()},
        {"can_move", u->canMove()}, {"can_attack", u->canAttack()},
        {"can_train", trains}, {"can_gather", gathers}, {"build_sites", builds}
      });
    }
    // Do not enumerate enemy()->getUnits(), inspect hidden properties, or enable CompleteMapInformation.
    for (auto u : Broodwar->getAllUnits()) {
      if (!u->exists() || !u->isVisible(self) || !u->getPlayer()->isEnemy(self) || !u->getPosition().isValid()) continue;
      enemies.push_back({{"id", u->getID()}, {"type", u->getType().getName()},
                         {"position", position(u->getPosition())}, {"hit_points", u->getHitPoints()}, {"visible", true}});
    }
    int index = 0;
    for (auto tile : Broodwar->getStartLocations()) {
      if (tile == homeTile) continue;
      locations.push_back({{"id", "start_" + std::to_string(index++)}, {"kind", "start"},
                           {"position", position(Position(tile) + Position(64, 48))},
                           {"explored", Broodwar->isExplored(tile.x, tile.y)}});
    }
    return {
      {"protocol_version", 1}, {"match_id", match}, {"frame", Broodwar->getFrameCount()},
      {"map_name", Broodwar->mapFileName()}, {"map_hash", Broodwar->mapHash()},
      {"self_race", self->getRace().getName()}, {"enemy_race", enemy ? enemy->getRace().getName() : "Unknown"},
      {"complete_map_information", Broodwar->isFlagEnabled(Flag::CompleteMapInformation)},
      {"minerals", self->minerals()}, {"gas", self->gas()},
      {"supply_used", self->supplyUsed() / 2}, {"supply_total", self->supplyTotal() / 2},
      {"home", position(home)}, {"units", own}, {"enemies", enemies},
      {"mineral_patches", minerals}, {"locations", locations}, {"destroyed_enemy_ids", destroyed},
      {"receipts", receipts}, {"counters", {
        {"gathered_minerals", self->gatheredMinerals()}, {"gathered_gas", self->gatheredGas()},
        {"spent_minerals", self->spentMinerals()}, {"spent_gas", self->spentGas()},
        {"units_killed", self->killedUnitCount()}, {"units_lost", self->deadUnitCount()},
        {"commands_attempted", attempted}, {"commands_accepted", accepted}, {"commands_effective", effective}
      }}
    };
  }

  void apply(const json& response) {
    const int frame = Broodwar->getFrameCount();
    const int observed = response.at("observed_frame").get<int>();
    const int expires = response.at("expires_frame").get<int>();
    json receipt = {{"decision_id", response.at("decision_id")}, {"frame", frame},
                    {"attempted", 0}, {"accepted", 0}, {"effective", 0}, {"reason", "ok"}};
    if (response.at("protocol_version") != 1 || response.at("match_id") != match ||
        observed != lastSent || observed <= lastAppliedObservation || observed > frame ||
        expires < frame || expires < observed || expires - observed > 480) {
      receipt["reason"] = "stale_or_wrong_match";
    } else {
      lastAppliedObservation = observed;
      const auto& commands = response.at("commands");
      if (!commands.is_array() || commands.size() > 1) throw std::runtime_error("invalid_commands");
      for (const auto& command : commands) {
        const std::string kind = command.at("kind").get<std::string>();
        const auto& ids = command.at("unit_ids");
        if (!ids.is_array() || ids.size() > 200) throw std::runtime_error("invalid_units");
        for (const auto& id : ids) {
          ++attempted;
          receipt["attempted"] = receipt["attempted"].get<int>() + 1;
          auto unit = Broodwar->getUnit(id.get<int>());
          if (!unit || !unit->exists() || unit->getPlayer() != Broodwar->self() || !unit->isCompleted()) {
            receipt["reason"] = "unit_unavailable"; continue;
          }
          UnitCommand action;
          bool valid = false;
          if (kind == "train") {
            const auto typeName = command.at("unit_type").get<std::string>();
            auto type = findUnitType(typeName);
            valid = type != UnitTypes::None && type != UnitTypes::Unknown && !unit->isTraining() && unit->canTrain(type);
            action = UnitCommand::train(unit, type);
          } else if (kind == "build") {
            const auto typeName = command.at("unit_type").get<std::string>();
            auto type = findUnitType(typeName);
            TilePosition tile(command.at("tile").at("x").get<int>(), command.at("tile").at("y").get<int>());
            valid = type != UnitTypes::None && type != UnitTypes::Unknown &&
                    tile.isValid() && unit->getType().isWorker() && !unit->isConstructing() && unit->canBuild(type, tile);
            action = UnitCommand::build(unit, tile, type);
          } else if (kind == "gather") {
            auto target = Broodwar->getUnit(command.at("target_id").get<int>());
            valid = target && target->exists() && target->isVisible() && (target->getType().isMineralField() || target->getType().isRefinery()) &&
                    !unit->isConstructing() && unit->canGather(target);
            if (valid) action = UnitCommand::gather(unit, target);
          } else if (kind == "attack" || kind == "move") {
            Position target(command.at("position").at("x").get<int>(), command.at("position").at("y").get<int>());
            valid = target.isValid() && !unit->isConstructing() &&
                    (kind == "attack" ? unit->canAttack(target) : unit->canMove());
            action = kind == "attack" ? UnitCommand::attack(unit, target) : UnitCommand::move(unit, target);
          } else if (kind == "repair") {
            auto target = Broodwar->getUnit(command.at("target_id").get<int>());
            valid = target && target->exists() && target->isVisible() && unit->canRepair(target);
            if (valid) action = UnitCommand::repair(unit, target);
          } else if (kind == "stop") {
            valid = unit->canStop();
            action = UnitCommand::stop(unit);
          } else if (kind == "hold_position") {
            valid = unit->canHoldPosition();
            action = UnitCommand::holdPosition(unit);
          } else if (kind == "siege") {
            valid = unit->canSiege();
            action = UnitCommand::siege(unit);
          } else if (kind == "unsiege") {
            valid = unit->canUnsiege();
            action = UnitCommand::unsiege(unit);
          } else if (kind == "cloak") {
            valid = unit->canCloak();
            action = UnitCommand::cloak(unit);
          } else if (kind == "decloak") {
            valid = unit->canDecloak();
            action = UnitCommand::decloak(unit);
          }
          if (!valid || !unit->canIssueCommand(action)) { receipt["reason"] = "revalidation_failed"; continue; }
          // Deduplicate continuous orders, but permit another completed training cycle.
          bool duplicate = kind != "train" && kind != "build" && action == unit->getLastCommand() && !unit->isIdle();
          if (duplicate) { receipt["reason"] = "duplicate_suppressed"; continue; }
          if (unit->issueCommand(action)) {
            ++accepted; ++effective;
            receipt["accepted"] = receipt["accepted"].get<int>() + 1;
            receipt["effective"] = receipt["effective"].get<int>() + 1;
          } else receipt["reason"] = "bwapi_rejected";
        }
      }
    }
    receipts.push_back(receipt);
    if (receipts.size() > 64) receipts.erase(receipts.begin());
  }

public:
  void onStart() override {
    if (pending.valid()) { try { pending.get(); } catch (...) {} }
    active = false;
    auto self = Broodwar->self();
    auto enemy = Broodwar->enemy();
    if (Broodwar->isReplay() || !self || !enemy || Broodwar->enemies().size() != 1 ||
        Broodwar->isFlagEnabled(Flag::CompleteMapInformation)) {
      Broodwar->printf("JevCraft requires a non-cheating 1v1 game.");
      return;
    }
    auto now = std::chrono::system_clock::now().time_since_epoch();
    match = "bwapi_" + std::to_string(GetCurrentProcessId()) + "_" +
            std::to_string(std::chrono::duration_cast<std::chrono::microseconds>(now).count());
    lastSent = -6; retryFrame = 0; lastAppliedObservation = -1;
    attempted = accepted = effective = 0;
    receipts = json::array(); destroyed.clear();
    // About 24 frames/second wall time. Do not run an unbounded fast game with a remote model.
    Broodwar->setLocalSpeed(42);
    active = true;
    Broodwar->printf("JevCraft bridge ready; start the Python service on port 8765.");
  }

  void onFrame() override {
    if (!active || Broodwar->isPaused()) return;
    if (Broodwar->isFlagEnabled(Flag::CompleteMapInformation)) { active = false; return; }
    const int frame = Broodwar->getFrameCount();
    if (pending.valid()) {
      if (pending.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready) return;
      try { apply(pending.get()); }
      catch (...) {
        Broodwar->printf("JevCraft request failed; keeping existing orders and retrying in 2 seconds.");
        retryFrame = frame + 48;
      }
    }
    if (frame < retryFrame || frame - lastSent < 6) return;
    try {
      auto observation = snapshot();
      lastSent = frame;
      // snapshot() is the last BWAPI access before dispatch; network threads only touch JSON.
      pending = std::async(std::launch::async, [observation]() { return postLocal(L"/step", observation); });
    } catch (...) { retryFrame = frame + 48; }
  }

  void onEnd(bool winner) override {
    if (!active) return;
    active = false;
    // Only match-end may wait; no in-game frame blocks on network inference.
    if (pending.valid()) { try { pending.get(); } catch (...) {} }
    try {
      auto observation = snapshot();
      observation["ended"] = true;
      observation["result"] = winner ? "win" : "loss";
      pending = std::async(std::launch::async, [observation]() { return postLocal(L"/end", observation); });
    } catch (...) {}
  }

  void onUnitDestroy(Unit unit) override {
    if (active && unit && unit->isVisible() && unit->getPlayer()->isEnemy(Broodwar->self()))
      destroyed.push_back(unit->getID());
  }
};
}

extern "C" __declspec(dllexport) void gameInit(BWAPI::Game* game) { BWAPI::BroodwarPtr = game; }
extern "C" __declspec(dllexport) BWAPI::AIModule* newAIModule() { return new Bridge(); }
BOOL APIENTRY DllMain(HMODULE, DWORD, LPVOID) { return TRUE; }
