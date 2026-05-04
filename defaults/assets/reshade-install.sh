#!/bin/bash

SEPARATOR="------------------------------------------------------------------------------------------------"
REQUIRED_EXECUTABLES=""
XDG_DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
MAIN_PATH=${MAIN_PATH:-"$XDG_DATA_HOME/decky-renodx/reshade"}
RESHADE_PATH="$MAIN_PATH/reshade"
WINE_MAIN_PATH="$(echo "$MAIN_PATH" | sed "s#/home/$USER/##" | sed 's#/#\\\\#g')"
RESHADE_VERSION=${RESHADE_VERSION:-"latest"}
RESHADE_ADDON_SUPPORT=${RESHADE_ADDON_SUPPORT:-1}
AUTOHDR_ENABLED=${AUTOHDR_ENABLED:-1}
GLOBAL_INI=${GLOBAL_INI:-"ReShade.ini"}

SCRIPT_DIR="$(dirname "$(realpath "$0")")"
if [[ "$SCRIPT_DIR" == */defaults/assets ]]; then
    PLUGIN_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
elif [[ "$SCRIPT_DIR" == */assets ]]; then
    PLUGIN_ROOT="$(dirname "$SCRIPT_DIR")"
else
    PLUGIN_ROOT="$(dirname "$SCRIPT_DIR")"
fi
BIN_PATH=${BIN_PATH:-"$PLUGIN_ROOT/bin"}
SEVENZIP=${SEVENZIP:-7z}

log_message() {
    echo "[DEBUG] $1" >&2
}

check_dependencies() {
    for cmd in $REQUIRED_EXECUTABLES; do
        if ! command -v "$cmd" &> /dev/null; then
            echo "Error: Required program '$cmd' is missing."
            exit 1
        fi
    done
}

setup_directories() {
    mkdir -p "$RESHADE_PATH"
    mkdir -p "$MAIN_PATH/ReShade_shaders/Merged/Shaders"
    mkdir -p "$MAIN_PATH/ReShade_shaders/Merged/Textures"
    mkdir -p "$MAIN_PATH/AutoHDR_addons"
}

create_temp_dir() {
    tmpDir=$(mktemp -d)
    cd "$tmpDir" || exit 1
}

remove_temp_dir() {
    cd "$MAIN_PATH" || exit 1
    [[ -d $tmpDir ]] && rm -rf "$tmpDir"
}

setup_d3dcompiler() {
    local arch=$1
    local target_file="$RESHADE_PATH/d3dcompiler_47.dll.$arch"
    [[ -f $target_file ]] && return

    if [[ ! -f "$BIN_PATH/d3dcompiler_47.dll" ]]; then
        log_message "Warning: d3dcompiler_47.dll not found; relying on Proton/Wine d3dcompiler"
        return
    fi

    cp "$BIN_PATH/d3dcompiler_47.dll" "$target_file"
}

setup_reshade() {
    create_temp_dir

    local installer_name=""
    local version_suffix=""
    if [[ "$RESHADE_VERSION" == "last" ]]; then
        version_suffix="_last_Addon"
        installer_name="reshade_last_addon.exe"
    else
        version_suffix="_latest_Addon"
        installer_name="reshade_latest_addon.exe"
    fi

    if [[ "$RESHADE_ADDON_SUPPORT" != "1" ]]; then
        log_message "Error: HDR runtime requires ReShade addon support"
        exit 1
    fi

    if [[ ! -f "$BIN_PATH/$installer_name" ]]; then
        log_message "Error: $installer_name not found in bin directory"
        exit 1
    fi

    if [[ ! -x "$SEVENZIP" ]] && ! command -v "$SEVENZIP" &> /dev/null; then
        log_message "Error: 7-Zip extractor not found: $SEVENZIP"
        exit 1
    fi

    cp "$BIN_PATH/$installer_name" "./ReShade_Setup.exe"
    "$SEVENZIP" -y e "./ReShade_Setup.exe" 1> /dev/null || {
        log_message "Failed to extract ReShade"
        remove_temp_dir
        exit 1
    }

    target_dir="$RESHADE_PATH/$RESHADE_VERSION$version_suffix"
    rm -rf "$target_dir"
    mkdir -p "$target_dir"
    mv ./* "$target_dir"
    remove_temp_dir

    ln -sfn "$target_dir" "$RESHADE_PATH/latest"
    echo "$RESHADE_VERSION$version_suffix" > "$RESHADE_PATH/LVERS"
    touch "$target_dir/addon_version"
}

setup_minimal_core() {
    local core_archive="$BIN_PATH/reshade_shaders.tar.gz"
    if [[ ! -f "$core_archive" ]]; then
        log_message "Core ReShade shader archive not found; continuing with AutoHDR only"
        return
    fi

    local temp_core_dir
    temp_core_dir=$(mktemp -d)
    tar -xzf "$core_archive" -C "$temp_core_dir"

    find "$temp_core_dir" -type f \( -name "*.fxh" -o -name "ReShade.fxh" \) -exec cp {} "$MAIN_PATH/ReShade_shaders/Merged/Shaders/" \; 2>/dev/null || true
    rm -rf "$temp_core_dir"
}

setup_autohdr() {
    if [[ "$AUTOHDR_ENABLED" != "1" ]]; then
        return
    fi

    if [[ -f "$BIN_PATH/autohdr_addon.tar.gz" ]]; then
        tar -xzf "$BIN_PATH/autohdr_addon.tar.gz" -C "$MAIN_PATH/AutoHDR_addons/"
        find "$MAIN_PATH/AutoHDR_addons" -type f \( -iname "*64.addon" -o -iname "*.addon64" \) -exec cp {} "$MAIN_PATH/AutoHDR_addons/AutoHDR.addon64" \; 2>/dev/null || true
        find "$MAIN_PATH/AutoHDR_addons" -type f \( -iname "*32.addon" -o -iname "*.addon32" \) -exec cp {} "$MAIN_PATH/AutoHDR_addons/AutoHDR.addon32" \; 2>/dev/null || true
    else
        log_message "Warning: autohdr_addon.tar.gz not found in bin directory"
    fi

    if [[ -f "$BIN_PATH/advanced_autohdr_effect.tar.gz" ]]; then
        local temp_autohdr_dir
        temp_autohdr_dir=$(mktemp -d)
        tar -xzf "$BIN_PATH/advanced_autohdr_effect.tar.gz" -C "$temp_autohdr_dir"

        if [[ -d "$temp_autohdr_dir/Shaders" ]]; then
            cp -rf "$temp_autohdr_dir/Shaders"/* "$MAIN_PATH/ReShade_shaders/Merged/Shaders/" 2>/dev/null || true
        fi
        if [[ -d "$temp_autohdr_dir/Textures" ]]; then
            cp -rf "$temp_autohdr_dir/Textures"/* "$MAIN_PATH/ReShade_shaders/Merged/Textures/" 2>/dev/null || true
        fi
        find "$temp_autohdr_dir" -maxdepth 1 -type f \( -name "*.fx" -o -name "*.fxh" \) -exec cp {} "$MAIN_PATH/ReShade_shaders/Merged/Shaders/" \; 2>/dev/null || true

        rm -rf "$temp_autohdr_dir"
    else
        log_message "Warning: advanced_autohdr_effect.tar.gz not found in bin directory"
    fi
}

setup_reshade_ini() {
    if [[ "$GLOBAL_INI" == "0" || "$GLOBAL_INI" != "ReShade.ini" || -f "$MAIN_PATH/$GLOBAL_INI" ]]; then
        return
    fi

    if [[ -f "$BIN_PATH/reshade_ini_template.ini" ]]; then
        cp "$BIN_PATH/reshade_ini_template.ini" "$MAIN_PATH/$GLOBAL_INI"
        sed -i "s/_USERSED_/$USER/g" "$MAIN_PATH/$GLOBAL_INI"
        sed -i "s#_SHADSED_#$WINE_MAIN_PATH\\\ReShade_shaders\\\Merged\\\Shaders#g" "$MAIN_PATH/$GLOBAL_INI"
        sed -i "s#_TEXSED_#$WINE_MAIN_PATH\\\ReShade_shaders\\\Merged\\\Textures#g" "$MAIN_PATH/$GLOBAL_INI"
    else
        cat > "$MAIN_PATH/$GLOBAL_INI" << EOF
[GENERAL]
EffectSearchPaths=$WINE_MAIN_PATH\\ReShade_shaders\\Merged\\Shaders
TextureSearchPaths=$WINE_MAIN_PATH\\ReShade_shaders\\Merged\\Textures
PresetPath=$WINE_MAIN_PATH\\ReShadePreset.ini
EOF
    fi
    chmod 666 "$MAIN_PATH/$GLOBAL_INI"
}

main() {
    echo -e "$SEPARATOR\nStarting HDR-only ReShade runtime installation...\n$SEPARATOR"
    log_message "Plugin root: $PLUGIN_ROOT"
    log_message "Bin path: $BIN_PATH"
    log_message "ReShade version: $RESHADE_VERSION"
    log_message "AutoHDR enabled: $AUTOHDR_ENABLED"

    check_dependencies
    setup_directories
    setup_reshade
    setup_d3dcompiler "32"
    setup_d3dcompiler "64"
    setup_minimal_core
    setup_autohdr
    setup_reshade_ini

    echo -e "$SEPARATOR\nHDR-only runtime installed successfully"
    echo "Extra shader packs are intentionally excluded."
    echo "AutoHDR addon path: $MAIN_PATH/AutoHDR_addons"
    echo "Effect path: $MAIN_PATH/ReShade_shaders/Merged/Shaders"
}

main "$@"
