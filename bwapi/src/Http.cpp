#include "Http.h"
#include <Windows.h>
#include <winhttp.h>
#include <memory>
#include <stdexcept>
#include <string>

namespace {
struct CloseHandle {
  void operator()(void* handle) const { if (handle) WinHttpCloseHandle(handle); }
};
using Handle = std::unique_ptr<void, CloseHandle>;
void require(bool success) { if (!success) throw std::runtime_error("bridge_transport_error"); }
}

nlohmann::json postLocal(const wchar_t* path, const nlohmann::json& payload) {
  Handle session(WinHttpOpen(L"JevCraft/0.1", WINHTTP_ACCESS_TYPE_NO_PROXY,
                            WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0));
  require(bool(session));
  // Remote Jev inference can take longer than one second, especially when
  // the staged value and policy requests are both in flight. Keep the game
  // thread asynchronous, but allow the worker enough time to receive a
  // valid response instead of turning normal provider latency into a retry.
  require(WinHttpSetTimeouts(session.get(), 1000, 1000, 15000, 15000) != FALSE);
  Handle connection(WinHttpConnect(session.get(), L"127.0.0.1", 8765, 0));
  require(bool(connection));
  Handle request(WinHttpOpenRequest(connection.get(), L"POST", path, nullptr,
                                    WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0));
  require(bool(request));
  DWORD redirect = WINHTTP_OPTION_REDIRECT_POLICY_NEVER;
  require(WinHttpSetOption(request.get(), WINHTTP_OPTION_REDIRECT_POLICY, &redirect, sizeof(redirect)) != FALSE);
  std::string body = payload.dump();
  require(body.size() <= 2000000);
  require(WinHttpSendRequest(request.get(), L"Content-Type: application/json\r\n", DWORD(-1),
                            body.data(), static_cast<DWORD>(body.size()), static_cast<DWORD>(body.size()), 0) != FALSE);
  require(WinHttpReceiveResponse(request.get(), nullptr) != FALSE);
  DWORD status = 0, size = sizeof(status);
  require(WinHttpQueryHeaders(request.get(), WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                             WINHTTP_HEADER_NAME_BY_INDEX, &status, &size, WINHTTP_NO_HEADER_INDEX) != FALSE);
  require(status == 200);
  std::string response;
  char buffer[8192];
  DWORD count = 0;
  do {
    require(WinHttpReadData(request.get(), buffer, sizeof(buffer), &count) != FALSE);
    response.append(buffer, count);
    require(response.size() <= 2000000);
  } while (count);
  return nlohmann::json::parse(response);
}
