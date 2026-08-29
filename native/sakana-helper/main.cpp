#include <windows.h>
#include <windowsx.h>
#include <shellapi.h>
#include <d3d11.h>
#include <dcomp.h>
#include <shlwapi.h>
#include <wrl.h>
#include <WebView2.h>
#include <chrono>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

using Microsoft::WRL::Callback;
using Microsoft::WRL::ComPtr;
namespace fs = std::filesystem;
#define RETURN_IF_FAILED(expr) do { const HRESULT _hr = (expr); if (FAILED(_hr)) return _hr; } while (0)

namespace {
constexpr int kCanvas = 360;
constexpr int kBottomMargin = 170;
constexpr UINT_PTR kOwnerTimer = 1;
constexpr wchar_t kVisibilityMessageName[] = L"EcommerceAgent.Sakana.SetVisible.v1";
constexpr int kBaseLeft = 124, kBaseTop = 256, kBaseRight = 236, kBaseBottom = 280;
// #sakana-widget's real CSS box (sakana.css: left:80px;top:80px;width:200px;height:200px) -
// the actual rendered character+base, used to clamp the draggable range. Deliberately
// smaller than the hit-test rect below, which is padded for easier clicking.
constexpr int kVisLeft = 80, kVisTop = 80, kVisRight = 280, kVisBottom = 280;

struct Options { HWND owner{}; fs::path assets; fs::path log; bool startHidden = false; };
Options g_options;
HWND g_hwnd{};
ComPtr<IDCompositionDevice> g_dcomp;
ComPtr<IDCompositionTarget> g_target;
ComPtr<IDCompositionVisual> g_root;
ComPtr<ICoreWebView2Controller> g_controller;
ComPtr<ICoreWebView2CompositionController> g_composition;
ComPtr<ICoreWebView2> g_webview;
EventRegistrationToken g_navigationToken{};
POINT g_rootPosition{0, 0}, g_dragCursor{}, g_dragRoot{};
bool g_positionInitialized = false;
bool g_ownerWasUsable = true;
bool g_userVisible = true;
UINT g_visibilityMessage = 0;

enum class Gesture { None, Character, Base };
Gesture g_gesture = Gesture::None;

const wchar_t* GestureName(Gesture g) {
  switch (g) {
    case Gesture::Character: return L"character";
    case Gesture::Base: return L"base";
    default: return L"none";
  }
}

void Log(const std::wstring& event, const std::wstring& data = L"") {
  try {
    fs::create_directories(g_options.log.parent_path());
    std::wofstream out(g_options.log, std::ios::app);
    out << L"{\"event\":\"" << event << L"\",\"data\":\"" << data << L"\"}\n";
  } catch (...) {}
}

std::wstring ArgValue(const std::vector<std::wstring>& args, const std::wstring& key) {
  for (size_t i = 0; i + 1 < args.size(); ++i) if (args[i] == key) return args[i + 1];
  return {};
}

Options ParseOptions() {
  int argc = 0;
  auto raw = CommandLineToArgvW(GetCommandLineW(), &argc);
  std::vector<std::wstring> args(raw, raw + argc);
  LocalFree(raw);
  Options value;
  auto owner = ArgValue(args, L"--owner-hwnd");
  auto assets = ArgValue(args, L"--asset-dir");
  auto log = ArgValue(args, L"--log");
  if (owner.empty() || assets.empty() || log.empty()) throw std::runtime_error("required arguments missing");
  value.owner = reinterpret_cast<HWND>(std::stoull(owner));
  value.assets = fs::absolute(assets);
  value.log = fs::absolute(log);
  value.startHidden = std::find(args.begin(), args.end(), L"--start-hidden") != args.end();
  if (!IsWindow(value.owner) || !fs::exists(value.assets / L"index.html")) throw std::runtime_error("owner or assets invalid");
  return value;
}

bool OwnerRect(RECT& result) {
  RECT client{}; POINT origin{};
  if (!GetClientRect(g_options.owner, &client) || !ClientToScreen(g_options.owner, &origin)) return false;
  result = {origin.x, origin.y, origin.x + client.right, origin.y + client.bottom};
  return true;
}

void SyncOwner() {
  if (!IsWindow(g_options.owner)) { Log(L"owner_lost"); DestroyWindow(g_hwnd); return; }
  const bool ownerUsable = g_userVisible && IsWindowVisible(g_options.owner) && !IsIconic(g_options.owner);
  if (ownerUsable != g_ownerWasUsable) {
    RECT wr{}; GetWindowRect(g_options.owner, &wr);
    std::wstringstream ss;
    ss << L"ownerUsable=" << ownerUsable << L" windowRect=(" << wr.left << L"," << wr.top << L"," << wr.right << L"," << wr.bottom
       << L") gesture=" << GestureName(g_gesture) << L" capture_self=" << (GetCapture() == g_hwnd ? L"yes" : L"no");
    Log(L"owner_usable_transition", ss.str());
    g_ownerWasUsable = ownerUsable;
  }
  if (!ownerUsable) {
    ShowWindow(g_hwnd, SW_HIDE); if (g_controller) g_controller->put_IsVisible(FALSE); return;
  }
  RECT r{}; if (!OwnerRect(r)) return;
  const int width = r.right - r.left, height = r.bottom - r.top;
  if (!g_positionInitialized) { g_rootPosition.y = std::max(0, height - kCanvas - kBottomMargin); g_positionInitialized = true; }
  // Clamp against the visible toy's bounding box (kVisLeft/Top/Right/Bottom), not the full
  // transparent kCanvas square: the canvas has ~24-80px of empty margin around the character
  // and base, so constraining the whole canvas to stay inside the owner leaves the visible toy
  // unable to ever reach the owner's real edges. Letting the transparent margin overhang the
  // owner is fine since it is click-through (WM_NCHITTEST returns HTTRANSPARENT there).
  const LONG minX = -kVisLeft, minY = -kVisTop;
  const LONG maxX = std::max(minX, static_cast<LONG>(width - kVisRight));
  const LONG maxY = std::max(minY, static_cast<LONG>(height - kVisBottom));
  g_rootPosition.x = std::clamp<LONG>(g_rootPosition.x, minX, maxX);
  g_rootPosition.y = std::clamp<LONG>(g_rootPosition.y, minY, maxY);
  SetWindowPos(g_hwnd, nullptr, r.left + g_rootPosition.x, r.top + g_rootPosition.y, kCanvas, kCanvas, SWP_NOACTIVATE | SWP_NOZORDER | SWP_SHOWWINDOW);
  if (g_controller) g_controller->put_IsVisible(TRUE);
}

COREWEBVIEW2_MOUSE_EVENT_VIRTUAL_KEYS MouseKeys(WPARAM value) {
  UINT keys = COREWEBVIEW2_MOUSE_EVENT_VIRTUAL_KEYS_NONE;
  if (value & MK_LBUTTON) keys |= COREWEBVIEW2_MOUSE_EVENT_VIRTUAL_KEYS_LEFT_BUTTON;
  if (value & MK_RBUTTON) keys |= COREWEBVIEW2_MOUSE_EVENT_VIRTUAL_KEYS_RIGHT_BUTTON;
  if (value & MK_SHIFT) keys |= COREWEBVIEW2_MOUSE_EVENT_VIRTUAL_KEYS_SHIFT;
  if (value & MK_CONTROL) keys |= COREWEBVIEW2_MOUSE_EVENT_VIRTUAL_KEYS_CONTROL;
  return static_cast<COREWEBVIEW2_MOUSE_EVENT_VIRTUAL_KEYS>(keys);
}

bool InBase(POINT p) { return p.x >= kBaseLeft && p.x < kBaseRight && p.y >= kBaseTop && p.y < kBaseBottom; }
bool InToy(POINT p) { return p.x >= 24 && p.x < 336 && p.y >= 24 && p.y < 300; }
const wchar_t* Classify(POINT p) { return InBase(p) ? L"base" : (InToy(p) ? L"character" : L"transparent"); }

void SendMouse(UINT message, WPARAM wparam, LPARAM lparam) {
  if (!g_composition) return;
  POINT p{GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam)};
  COREWEBVIEW2_MOUSE_EVENT_KIND kind;
  switch (message) {
    case WM_MOUSEMOVE: kind = COREWEBVIEW2_MOUSE_EVENT_KIND_MOVE; break;
    case WM_LBUTTONDOWN: kind = COREWEBVIEW2_MOUSE_EVENT_KIND_LEFT_BUTTON_DOWN; break;
    case WM_LBUTTONUP: kind = COREWEBVIEW2_MOUSE_EVENT_KIND_LEFT_BUTTON_UP; break;
    default: return;
  }
  g_composition->SendMouseInput(kind, MouseKeys(wparam), 0, p);
}

LRESULT CALLBACK WindowProc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
  if (g_visibilityMessage != 0 && message == g_visibilityMessage) {
    g_userVisible = wparam != 0;
    Log(L"visibility", g_userVisible ? L"show" : L"hide");
    SyncOwner();
    return 0;
  }
  switch (message) {
    case WM_TIMER: SyncOwner(); return 0;
    case WM_NCHITTEST: {
      POINT p{GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam)}; ScreenToClient(hwnd, &p);
      return InToy(p) ? HTCLIENT : HTTRANSPARENT;
    }
    case WM_MOUSEACTIVATE: return MA_NOACTIVATE;
    case WM_LBUTTONDOWN: {
      POINT p{GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam)};
      POINT screen{}; GetCursorPos(&screen);
      std::wstringstream ss;
      ss << L"hit=" << Classify(p) << L" client=(" << p.x << L"," << p.y << L") screen=(" << screen.x << L"," << screen.y
         << L") gesture_before=" << GestureName(g_gesture) << L" capture_before=" << (GetCapture() == hwnd ? L"self" : (GetCapture() ? L"other" : L"none"));
      Log(L"lbuttondown", ss.str());
      if (InBase(p)) { g_gesture = Gesture::Base; g_dragCursor = screen; g_dragRoot = g_rootPosition; SetCapture(hwnd); return 0; }
      g_gesture = Gesture::Character; SetCapture(hwnd); SendMouse(message, wparam, lparam); return 0;
    }
    case WM_MOUSEMOVE:
      if (g_gesture == Gesture::Base) { POINT now{}; GetCursorPos(&now); g_rootPosition = {g_dragRoot.x + now.x - g_dragCursor.x, g_dragRoot.y + now.y - g_dragCursor.y}; SyncOwner(); return 0; }
      if (g_gesture == Gesture::Character && !(wparam & MK_LBUTTON)) {
        std::wstringstream ss; ss << L"client=(" << GET_X_LPARAM(lparam) << L"," << GET_Y_LPARAM(lparam) << L") capture_self=" << (GetCapture() == hwnd ? L"yes" : L"no");
        Log(L"button_lost_during_move", ss.str());
      }
      SendMouse(message, wparam, lparam); return 0;
    case WM_LBUTTONUP: {
      POINT p{GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam)};
      std::wstringstream ss; ss << L"hit=" << Classify(p) << L" client=(" << p.x << L"," << p.y << L") gesture=" << GestureName(g_gesture);
      if (g_gesture == Gesture::Base) {
        RECT r{}; OwnerRect(r);
        ss << L" root=(" << g_rootPosition.x << L"," << g_rootPosition.y << L") ownerClient=(" << (r.right - r.left) << L"x" << (r.bottom - r.top) << L")";
      }
      Log(L"lbuttonup", ss.str());
      if (g_gesture == Gesture::Base) { g_gesture = Gesture::None; ReleaseCapture(); return 0; }
      SendMouse(message, wparam, lparam); g_gesture = Gesture::None; ReleaseCapture(); return 0;
    }
    case WM_CAPTURECHANGED: {
      std::wstringstream ss;
      ss << L"gesture=" << GestureName(g_gesture) << L" new_capture_hwnd=" << reinterpret_cast<uintptr_t>(reinterpret_cast<HWND>(lparam));
      Log(L"capturechanged", ss.str());
      return DefWindowProcW(hwnd, message, wparam, lparam);
    }
    case WM_CANCELMODE: {
      std::wstringstream ss; ss << L"gesture=" << GestureName(g_gesture) << L" capture_self=" << (GetCapture() == hwnd ? L"yes" : L"no");
      Log(L"cancelmode", ss.str());
      return DefWindowProcW(hwnd, message, wparam, lparam);
    }
    case WM_MOUSELEAVE:
      if (g_composition) g_composition->SendMouseInput(COREWEBVIEW2_MOUSE_EVENT_KIND_LEAVE, COREWEBVIEW2_MOUSE_EVENT_VIRTUAL_KEYS_NONE, 0, {});
      return 0;
    case WM_DESTROY:
      KillTimer(hwnd, kOwnerTimer); if (g_controller) g_controller->Close(); PostQuitMessage(0); return 0;
  }
  return DefWindowProcW(hwnd, message, wparam, lparam);
}

HRESULT InitializeComposition() {
  ComPtr<ID3D11Device> d3d;
  D3D_FEATURE_LEVEL level{};
  RETURN_IF_FAILED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION, &d3d, &level, nullptr));
  ComPtr<IDXGIDevice> dxgi; RETURN_IF_FAILED(d3d.As(&dxgi));
  RETURN_IF_FAILED(DCompositionCreateDevice(dxgi.Get(), IID_PPV_ARGS(&g_dcomp)));
  RETURN_IF_FAILED(g_dcomp->CreateTargetForHwnd(g_hwnd, TRUE, &g_target));
  RETURN_IF_FAILED(g_dcomp->CreateVisual(&g_root));
  RETURN_IF_FAILED(g_target->SetRoot(g_root.Get()));
  return g_dcomp->Commit();
}

void InitializeWebView() {
  auto udf = fs::path(_wgetenv(L"LOCALAPPDATA")) / L"EcommerceAgent" / L"sakana_webview2";
  fs::create_directories(udf);
  CreateCoreWebView2EnvironmentWithOptions(nullptr, udf.c_str(), nullptr,
    Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>([](HRESULT hr, ICoreWebView2Environment* env) -> HRESULT {
      if (FAILED(hr) || !env) { Log(L"environment_failed", std::to_wstring(hr)); return hr; }
      ComPtr<ICoreWebView2Environment3> env3;
      if (FAILED(env->QueryInterface(IID_PPV_ARGS(&env3)))) { Log(L"composition_api_missing"); return E_NOINTERFACE; }
      return env3->CreateCoreWebView2CompositionController(g_hwnd,
        Callback<ICoreWebView2CreateCoreWebView2CompositionControllerCompletedHandler>([](HRESULT result, ICoreWebView2CompositionController* composition) -> HRESULT {
          if (FAILED(result) || !composition) { Log(L"controller_failed", std::to_wstring(result)); return result; }
          g_composition = composition; composition->QueryInterface(IID_PPV_ARGS(&g_controller));
          composition->put_RootVisualTarget(g_root.Get());
          g_controller->put_Bounds({0, 0, kCanvas, kCanvas});
          ComPtr<ICoreWebView2Controller2> controller2; if (SUCCEEDED(g_controller.As(&controller2))) controller2->put_DefaultBackgroundColor({0,0,0,0});
          g_controller->get_CoreWebView2(&g_webview);
          ComPtr<ICoreWebView2_3> web3; if (SUCCEEDED(g_webview.As(&web3))) web3->SetVirtualHostNameToFolderMapping(L"sakana.local", g_options.assets.c_str(), COREWEBVIEW2_HOST_RESOURCE_ACCESS_KIND_DENY_CORS);
          g_webview->add_NavigationCompleted(Callback<ICoreWebView2NavigationCompletedEventHandler>([](ICoreWebView2*, ICoreWebView2NavigationCompletedEventArgs* args) -> HRESULT { BOOL ok{}; args->get_IsSuccess(&ok); Log(ok ? L"navigation_ok" : L"navigation_failed"); return S_OK; }).Get(), &g_navigationToken);
          EventRegistrationToken webMessageToken{};
          g_webview->add_WebMessageReceived(Callback<ICoreWebView2WebMessageReceivedEventHandler>([](ICoreWebView2*, ICoreWebView2WebMessageReceivedEventArgs* args) -> HRESULT {
            LPWSTR json{};
            if (SUCCEEDED(args->get_WebMessageAsJson(&json)) && json) { Log(L"webmessage", json); CoTaskMemFree(json); }
            return S_OK;
          }).Get(), &webMessageToken);
          g_webview->Navigate(L"https://sakana.local/index.html");
          g_dcomp->Commit(); Log(L"controller_ready", L"composition"); SyncOwner(); return S_OK;
        }).Get());
    }).Get());
}
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
  try { g_options = ParseOptions(); }
  catch (...) { return 2; }
  g_userVisible = !g_options.startHidden;
  g_visibilityMessage = RegisterWindowMessageW(kVisibilityMessageName);
  CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  WNDCLASSW wc{}; wc.lpfnWndProc = WindowProc; wc.hInstance = instance; wc.lpszClassName = L"EcommerceAgentSakanaComposition"; wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
  RegisterClassW(&wc);
  g_hwnd = CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_NOREDIRECTIONBITMAP, wc.lpszClassName, L"", WS_POPUP, 0, 0, kCanvas, kCanvas, g_options.owner, nullptr, instance, nullptr);
  if (!g_hwnd || FAILED(InitializeComposition())) return 3;
  {
    RECT owr{}; POINT owOrigin{};
    GetClientRect(g_options.owner, &owr); ClientToScreen(g_options.owner, &owOrigin);
    RECT owScreen{}; GetWindowRect(g_options.owner, &owScreen);
    std::wstringstream ss;
    ss << L"ownerHwnd=" << reinterpret_cast<uintptr_t>(g_options.owner) << L" ownerClient=(" << (owr.right - owr.left) << L"x" << (owr.bottom - owr.top)
       << L") ownerOrigin=(" << owOrigin.x << L"," << owOrigin.y << L") ownerWindowRect=(" << owScreen.left << L"," << owScreen.top << L"," << owScreen.right << L"," << owScreen.bottom
       << L") visible=" << IsWindowVisible(g_options.owner) << L" iconic=" << IsIconic(g_options.owner);
    Log(L"helper_start", ss.str());
  }
  InitializeWebView(); SetTimer(g_hwnd, kOwnerTimer, 33, nullptr); SyncOwner();
  MSG msg{}; while (GetMessageW(&msg, nullptr, 0, 0) > 0) { TranslateMessage(&msg); DispatchMessageW(&msg); }
  CoUninitialize(); return 0;
}
