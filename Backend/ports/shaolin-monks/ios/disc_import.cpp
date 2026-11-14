#include "disc_import.h"
#include <CommonCrypto/CommonDigest.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <cerrno>
#include <fcntl.h>
#include <memory>
#include <unordered_map>
#include <unistd.h>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;
namespace {
constexpr uint64_t sectorSize = 2048;
constexpr uint64_t maximumDirectory = 32 * 1024 * 1024;
constexpr uint64_t maximumBoot = 32 * 1024 * 1024;
struct Extent {
    fs::path path;
    uint64_t offset;
    uint64_t size;
    bool directory;
};

uint32_t dual32(const uint8_t *bytes) {
    uint32_t little = 0, big = 0;
    for (int i = 0; i < 4; ++i) {
        little |= static_cast<uint32_t>(bytes[i]) << (8 * i);
        big = (big << 8) | bytes[4 + i];
    }
    if (little != big) throw std::runtime_error("The disc has an invalid ISO extent.");
    return little;
}

std::string sha256(const void *data, uint64_t size) {
    std::array<unsigned char, CC_SHA256_DIGEST_LENGTH> digest{};
    CC_SHA256(data, static_cast<CC_LONG>(size), digest.data());
    constexpr char hex[] = "0123456789abcdef";
    std::string result;
    for (unsigned char value : digest) {
        result += hex[value >> 4];
        result += hex[value & 15];
    }
    return result;
}

std::string isoName(const uint8_t *record, std::size_t length) {
    std::string name(reinterpret_cast<const char *>(record), length);
    if (auto separator = name.find(';'); separator != std::string::npos) {
        if (separator + 1 == name.size() || name.find_first_not_of("0123456789", separator + 1) != std::string::npos)
            throw std::runtime_error("The disc has an invalid ISO file version.");
        name.resize(separator);
    }
    if (name.size() > 1 && name.back() == '.') name.pop_back();
    constexpr std::string_view allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-. ";
    if (name.empty() || name == "." || name == ".." || name.find_first_not_of(allowed) != std::string::npos)
        throw std::runtime_error("The disc has an unsupported ISO filename.");
    return name;
}

class Disc {
public:
    explicit Disc(const fs::path &path) : stream(path, std::ios::binary), size(fs::file_size(path)) {
        if (!stream) throw std::runtime_error("Could not read the original PS2 ISO.");
    }

    void read(uint64_t offset, void *data, std::size_t length) {
        if (offset > size || length > size - offset) throw std::runtime_error("The ISO is incomplete.");
        stream.seekg(static_cast<std::streamoff>(offset));
        if (!stream.read(static_cast<char *>(data), static_cast<std::streamsize>(length)))
            throw std::runtime_error("The disc could not be read. Copy the ISO to this device first.");
    }

    std::vector<Extent> files() {
        std::array<uint8_t, sectorSize> block{};
        for (uint64_t sector = 16; sector < 64 && (sector + 1) * sectorSize <= size; ++sector) {
            read(sector * sectorSize, block.data(), block.size());
            if (std::memcmp(block.data() + 1, "CD001\x01", 6) != 0 || block[0] == 255) break;
            if (block[0] == 1) {
                std::vector<Extent> result;
                collect(extent(block.data() + 156, block.size() - 156, {}), result, 0);
                return result;
            }
        }
        throw std::runtime_error("Use an uncompressed PS2 ISO with 2048-byte sectors.");
    }

private:
    Extent extent(const uint8_t *data, std::size_t bytes, fs::path path) {
        if (bytes < 34 || data[0] < 34 || data[0] > bytes || 33 + data[32] > data[0])
            throw std::runtime_error("The disc directory record is damaged.");
        if (data[25] & 0x80 || data[26] || data[27])
            throw std::runtime_error("Split or interleaved ISO files are unsupported.");
        uint64_t offset = (static_cast<uint64_t>(dual32(data + 2)) + data[1]) * sectorSize;
        uint64_t length = dual32(data + 10);
        if (offset > size || length > size - offset) throw std::runtime_error("A file extends past the ISO.");
        return {std::move(path), offset, length, (data[25] & 2) != 0};
    }

    void collect(const Extent &directory, std::vector<Extent> &files, int depth) {
        if (!directory.directory || !directory.size || directory.size > maximumDirectory || depth > 16)
            throw std::runtime_error("The disc directory tree is invalid.");
        if (directories.size() >= 16384 || !directories.emplace(directory.offset, directory.size).second)
            throw std::runtime_error("The disc contains a repeated or invalid directory extent.");
        std::array<uint8_t, sectorSize> block{};
        std::set<std::string> names;
        for (uint64_t offset = 0; offset < directory.size; offset += sectorSize) {
            auto count = static_cast<std::size_t>(std::min(sectorSize, directory.size - offset));
            read(directory.offset + offset, block.data(), count);
            for (std::size_t i = 0; i < count && block[i];) {
                const uint8_t *data = block.data() + i;
                Extent file = extent(data, count - i, directory.path);
                i += data[0];
                if (data[32] == 1 && data[33] <= 1) continue;
                auto name = isoName(data + 33, data[32]);
                std::string key = name;
                for (char &c : key) if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
                if (!names.insert(key).second) throw std::runtime_error("The ISO contains duplicate file names.");
                file.path /= name;
                if (file.directory) collect(file, files, depth + 1);
                else {
                    if (files.size() >= 65536) throw std::runtime_error("The ISO contains too many files.");
                    files.push_back(std::move(file));
                }
            }
        }
    }

    std::ifstream stream;
    uint64_t size;
    std::set<std::pair<uint64_t, uint64_t>> directories;
};


std::string discKey(std::string_view name) {
    std::string key;
    for (char c : name) {
        if (c == ';') break;
        if (c == '\\') c = '/';
        if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
        if (c == '/' && (key.empty() || key.back() == '/')) continue;
        key += c;
    }
    return key;
}

struct MountedDisc {
    MountedDisc(const fs::path &iso, fs::path root, const std::vector<Extent> &files)
        : root(std::move(root)), size(fs::file_size(iso)) {
        for (const auto &file : files) {
            entries.emplace(discKey(file.path.generic_string()), file);
            auto leaf = discKey(file.path.filename().generic_string());
            if (!leaves.try_emplace(leaf, file.offset, file.size).second) leaves[leaf].second = 0;
        }
        fd = ::open(iso.c_str(), O_RDONLY | O_CLOEXEC);
        if (fd < 0) throw std::runtime_error("Could not open the original PS2 ISO.");
    }
    ~MountedDisc() { ::close(fd); }
    bool read(uint64_t offset, void *destination, std::size_t bytes) const {
        if (offset > size || bytes > size - offset) return false;
        auto *out = static_cast<uint8_t *>(destination);
        while (bytes) {
            ssize_t n = ::pread(fd, out, bytes, static_cast<off_t>(offset));
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) return false;
            out += n;
            bytes -= static_cast<std::size_t>(n);
            offset += static_cast<uint64_t>(n);
        }
        return true;
    }
    fs::path root;
    uint64_t size;
    int fd = -1;
    std::unordered_map<std::string, Extent> entries;
    std::unordered_map<std::string, std::pair<uint64_t, uint64_t>> leaves;
};
std::unique_ptr<MountedDisc> mounted;

bool lookup(std::string_view path, uint64_t &offset, uint64_t &size) {
    if (!mounted) return false;
    auto key = discKey(path);
    auto it = mounted->entries.find(key);
    if (it != mounted->entries.end()) {
        offset = it->second.offset;
        size = it->second.size;
        return true;
    }
    // Some game APIs use a bare name for a file stored in a subdirectory.
    if (key.find('/') == std::string::npos) {
        auto leaf = mounted->leaves.find(key);
        if (leaf != mounted->leaves.end() && leaf->second.second) {
            offset = leaf->second.first;
            size = leaf->second.second;
            return true;
        }
    }
    return false;
}

struct DiscStream {
    uint64_t start, size, position = 0;
};
std::size_t readStream(DiscStream &stream, char *data, std::size_t requested) {
    if (stream.position >= stream.size) return 0;
    auto count = static_cast<std::size_t>(std::min<uint64_t>(requested, stream.size - stream.position));
    if (!mounted->read(stream.start + stream.position, data, count)) {
        errno = EIO;
        return SIZE_MAX;
    }
    stream.position += count;
    return count;
}
int64_t seekStream(DiscStream &stream, int64_t offset, int whence) {
    int64_t base = 0, next = 0;
    if (whence == SEEK_CUR) base = static_cast<int64_t>(stream.position);
    else if (whence == SEEK_END) base = static_cast<int64_t>(stream.size);
    else if (whence != SEEK_SET) { errno = EINVAL; return -1; }
    if (__builtin_add_overflow(base, offset, &next) || next < 0) { errno = EINVAL; return -1; }
    stream.position = static_cast<uint64_t>(next);
    return next;
}
int closeStream(void *cookie) {
    delete static_cast<DiscStream *>(cookie);
    return 0;
}
} // namespace

fs::path mountPS2Disc(const fs::path &iso, const fs::path &cache,
                     std::string_view bootName, std::string_view expectedBootHash) {
    Disc disc(iso);
    auto files = disc.files();
    auto elf = std::find_if(files.begin(), files.end(), [&](const Extent &entry) {
        return discKey(entry.path.generic_string()) == discKey(bootName);
    });
    if (elf == files.end() || !elf->size || elf->size > maximumBoot)
        throw std::runtime_error("The ISO does not contain the required PS2 boot executable.");
    std::vector<char> bootBytes(static_cast<std::size_t>(elf->size));
    auto next = std::make_unique<MountedDisc>(iso, cache, files);
    if (!next->read(elf->offset, bootBytes.data(), bootBytes.size()))
        throw std::runtime_error("The ISO is incomplete or unavailable.");
    if (sha256(bootBytes.data(), bootBytes.size()) != expectedBootHash)
        throw std::runtime_error("This ISO revision does not match the IPA. Use the disc selected in PS2Apple.");

    fs::create_directories(cache);
    auto boot = cache / std::string(bootName);
    bool cached = false;
    if (fs::is_regular_file(boot) && fs::file_size(boot) == bootBytes.size()) {
        std::vector<char> prior(bootBytes.size());
        std::ifstream input(boot, std::ios::binary);
        cached = input.read(prior.data(), prior.size()) && prior == bootBytes;
    }
    if (!cached) {
        auto stage = boot;
        stage += ".partial";
        try {
            std::ofstream output(stage, std::ios::binary | std::ios::trunc);
            if (!output.write(bootBytes.data(), bootBytes.size()))
                throw std::runtime_error("Could not cache the boot ELF. Check available storage.");
            output.close();
            if (!output) throw std::runtime_error("Could not finish caching the boot ELF.");
            fs::rename(stage, boot);
        } catch (...) {
            std::error_code error;
            fs::remove(stage, error);
            throw;
        }
    }
    mounted = std::move(next);
    return boot;
}

bool ps2DiscMounted() { return mounted != nullptr; }

bool ps2DiscLookup(std::string_view relativePath, uint32_t &sector, uint32_t &size) {
    uint64_t offset = 0, bytes = 0;
    if (!lookup(relativePath, offset, bytes)) return false;
    sector = static_cast<uint32_t>(offset / sectorSize);
    size = static_cast<uint32_t>(bytes);
    return true;
}

bool ps2DiscReadSectors(uint32_t sector, uint32_t count, void *destination, std::size_t size) {
    uint64_t offset = uint64_t(sector) * sectorSize;
    uint64_t bytes = uint64_t(count) * sectorSize;
    return mounted && destination && size <= bytes && offset <= mounted->size &&
        bytes <= mounted->size - offset && mounted->read(offset, destination, size);
}

std::FILE *ps2DiscOpen(const fs::path &path, uint64_t *size) {
    if (!mounted) return nullptr;
    fs::path relative = path.lexically_normal().lexically_relative(mounted->root);
    if (relative.empty() || relative.is_absolute() || *relative.begin() == "..") return nullptr;
    uint64_t offset = 0, bytes = 0;
    if (!lookup(relative.generic_string(), offset, bytes)) return nullptr;
    auto cookie = std::make_unique<DiscStream>(DiscStream{offset, bytes});
#if defined(__APPLE__)
    std::FILE *file = funopen(cookie.get(),
        [](void *state, char *data, int count) -> int {
            auto n = readStream(*static_cast<DiscStream *>(state), data, count);
            return n == SIZE_MAX ? -1 : static_cast<int>(n);
        }, nullptr,
        [](void *state, fpos_t offset, int whence) -> fpos_t {
            return seekStream(*static_cast<DiscStream *>(state), offset, whence);
        }, closeStream);
#else
    cookie_io_functions_t io{};
    io.read = [](void *state, char *data, size_t count) -> ssize_t {
        auto n = readStream(*static_cast<DiscStream *>(state), data, count);
        return n == SIZE_MAX ? -1 : static_cast<ssize_t>(n);
    };
    io.seek = [](void *state, off64_t *offset, int whence) -> int {
        int64_t result = seekStream(*static_cast<DiscStream *>(state), *offset, whence);
        if (result < 0) return -1;
        *offset = result;
        return 0;
    };
    io.close = closeStream;
    std::FILE *file = fopencookie(cookie.get(), "rb", io);
#endif
    if (!file) return nullptr;
    cookie.release();
    if (size) *size = bytes;
    return file;
}
