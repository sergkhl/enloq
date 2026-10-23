// Q32: the app imports pure modules from ../shared by path, so Metro watches
// that folder beside the project's own.
const path = require("node:path");
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);
config.watchFolders = [
  ...(config.watchFolders ?? []),
  path.resolve(__dirname, "../shared"),
];
// Shared files resolve their imports (Babel's helpers too) from the app.
config.resolver.nodeModulesPaths = [
  ...(config.resolver.nodeModulesPaths ?? []),
  path.resolve(__dirname, "node_modules"),
];
module.exports = config;
