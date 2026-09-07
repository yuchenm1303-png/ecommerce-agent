#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>

#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {
constexpr int kSetupResourceId = 101;
constexpr wchar_t kDefaultInstallDir[] = L"Smirel.ListingStudio";
constexpr wchar_t kMainExe[] = L"EcommerceAgent.exe";

std::wstring quote_arg(const std::wstring& value) {
    if (value.empty()) return L"\"\"";
    if (value.find_first_of(L" \t\"") == std::wstring::npos) return value;

    std::wstring out = L"\"";
    size_t backslashes = 0;
    for (wchar_t ch : value) {
        if (ch == L'\\') {
            ++backslashes;
            continue;
        }
        if (ch == L'\"') {
            out.append(backslashes * 2 + 1, L'\\');
            out.push_back(L'\"');
            backslashes = 0;
            continue;
        }
        out.append(backslashes, L'\\');
        backslashes = 0;
        out.push_back(ch);
    }
    out.append(backslashes * 2, L'\\');
    out.push_back(L'\"');
    return out;
}

std::wstring forwarded_command_line(int argc, wchar_t** argv) {
    std::wstring command;
    for (int i = 1; i < argc; ++i) {
        if (!command.empty()) command.push_back(L' ');
        command += quote_arg(argv[i]);
    }
    return command;
}

fs::path default_install_root() {
    PWSTR raw = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_DEFAULT, nullptr, &raw)) || raw == nullptr) {
        throw std::runtime_error("LocalAppData unavailable");
    }
    fs::path root(raw);
    CoTaskMemFree(raw);
    return root / kDefaultInstallDir;
}

fs::path requested_install_root(int argc, wchar_t** argv) {
    for (int i = 1; i < argc; ++i) {
        std::wstring arg(argv[i]);
        if (_wcsicmp(arg.c_str(), L"--installto") == 0 && i + 1 < argc) {
            return fs::path(argv[i + 1]);
        }
        constexpr wchar_t prefix[] = L"--installto=";
        if (arg.size() > std::size(prefix) - 1 && _wcsnicmp(arg.c_str(), prefix, std::size(prefix) - 1) == 0) {
            return fs::path(arg.substr(std::size(prefix) - 1));
        }
    }
    return default_install_root();
}

bool directory_has_entries(const fs::path& root) {
    std::error_code ec;
    if (!fs::is_directory(root, ec)) return false;
    return fs::directory_iterator(root, ec) != fs::directory_iterator();
}

bool is_complete_install(const fs::path& root) {
    std::error_code ec;
    return fs::is_regular_file(root / L"Update.exe", ec)
        && fs::is_regular_file(root / kMainExe, ec)
        && fs::is_regular_file(root / L"current" / kMainExe, ec);
}

fs::path stale_backup_path(const fs::path& root) {
    const auto stamp = GetTickCount64();
    return root.parent_path() /
        (root.filename().wstring() + L".stale-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(stamp));
}

fs::path make_temp_dir() {
    wchar_t buffer[MAX_PATH + 1]{};
    const DWORD len = GetTempPathW(MAX_PATH, buffer);
    if (len == 0 || len > MAX_PATH) throw std::runtime_error("temp path unavailable");
    fs::path dir = fs::path(buffer) /
        (L"Smirel.ListingStudio.Setup-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64()));
    fs::create_directories(dir);
    return dir;
}

fs::path extract_native_setup(const fs::path& temp_dir) {
    HRSRC resource = FindResourceW(nullptr, MAKEINTRESOURCEW(kSetupResourceId), RT_RCDATA);
    if (!resource) throw std::runtime_error("setup resource missing");
    HGLOBAL loaded = LoadResource(nullptr, resource);
    if (!loaded) throw std::runtime_error("setup resource load failed");
    const DWORD size = SizeofResource(nullptr, resource);
    const void* bytes = LockResource(loaded);
    if (!bytes || size == 0) throw std::runtime_error("setup resource empty");

    fs::path setup = temp_dir / L"ListingStudio-Velopack-Setup.exe";
    std::ofstream output(setup, std::ios::binary | std::ios::trunc);
    if (!output) throw std::runtime_error("setup extraction failed");
    output.write(static_cast<const char*>(bytes), static_cast<std::streamsize>(size));
    output.close();
    if (!output) throw std::runtime_error("setup extraction incomplete");
    return setup;
}

DWORD run_native_setup(const fs::path& setup, const std::wstring& forwarded) {
    std::wstring command = quote_arg(setup.wstring());
    if (!forwarded.empty()) {
        command.push_back(L' ');
        command += forwarded;
    }
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(L'\0');

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    if (!CreateProcessW(setup.c_str(), mutable_command.data(), nullptr, nullptr, FALSE, 0, nullptr, nullptr, &si, &pi)) {
        throw std::runtime_error("native setup launch failed");
    }
    CloseHandle(pi.hThread);
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD exit_code = ERROR_GEN_FAILURE;
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hProcess);
    return exit_code;
}

void remove_tree_noexcept(const fs::path& path) {
    std::error_code ec;
    fs::remove_all(path, ec);
}

void restore_stale_install(const fs::path& root, const fs::path& backup) {
    if (backup.empty()) return;
    std::error_code ec;
    if (fs::exists(root, ec)) fs::remove_all(root, ec);
    ec.clear();
    if (fs::exists(backup, ec)) fs::rename(backup, root, ec);
}

void show_error(const wchar_t* message) {
    MessageBoxW(nullptr, message, L"Listing Studio 安装程序", MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
}
}  // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) {
        show_error(L"安装程序无法读取启动参数，请重新下载后重试。");
        return ERROR_INVALID_PARAMETER;
    }

    fs::path install_root;
    fs::path stale_backup;
    fs::path temp_dir;
    try {
        install_root = requested_install_root(argc, argv);
        const bool stale = directory_has_entries(install_root) && !is_complete_install(install_root);
        if (stale) {
            stale_backup = stale_backup_path(install_root);
            std::error_code ec;
            fs::rename(install_root, stale_backup, ec);
            if (ec) {
                LocalFree(argv);
                show_error(L"检测到旧安装残留，但无法自动整理。请关闭 Listing Studio 后重新双击安装包。");
                return ERROR_SHARING_VIOLATION;
            }
        }

        temp_dir = make_temp_dir();
        const fs::path setup = extract_native_setup(temp_dir);
        const std::wstring forwarded = forwarded_command_line(argc, argv);
        LocalFree(argv);
        argv = nullptr;

        const DWORD exit_code = run_native_setup(setup, forwarded);
        if (exit_code == ERROR_SUCCESS) {
            remove_tree_noexcept(stale_backup);
        } else {
            restore_stale_install(install_root, stale_backup);
        }
        remove_tree_noexcept(temp_dir);
        return static_cast<int>(exit_code);
    } catch (...) {
        if (argv) LocalFree(argv);
        restore_stale_install(install_root, stale_backup);
        remove_tree_noexcept(temp_dir);
        show_error(L"安装程序无法准备安装环境，请重新下载后重试。");
        return ERROR_INSTALL_FAILURE;
    }
}
