#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <string_view>

std::filesystem::path mountPS2Disc(const std::filesystem::path &iso,
                                   const std::filesystem::path &cache,
                                   std::string_view bootName,
                                   std::string_view expectedBootHash);
bool ps2DiscMounted();
bool ps2DiscLookup(std::string_view relativePath, uint32_t &sector, uint32_t &size);
bool ps2DiscReadSectors(uint32_t sector, uint32_t count, void *destination, std::size_t size);
std::FILE *ps2DiscOpen(const std::filesystem::path &path, uint64_t *size = nullptr);
