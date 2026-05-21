{
  description = "MCP bridge connecting Kimi Code CLI to scheme-langserver";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonEnv = pkgs.python313;
        in
        {
          default = pkgs.mkShell {
            packages = [
              pythonEnv
              pkgs.uv
            ] ++ pkgs.lib.optional (pkgs ? scheme-langserver) pkgs.scheme-langserver;

            shellHook = ''
              echo "scheme-langserver-bridge dev shell"
              echo "Python: $(python3 --version)"
              echo "uv: $(uv --version)"
              if command -v scheme-langserver &>/dev/null; then
                echo "scheme-langserver: $(scheme-langserver --help 2>&1 | head -1)"
              else
                echo "scheme-langserver: not in PATH (set SCHEME_LANGSERVER_PATH to your binary)"
              fi
            '';
          };
        });

      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonEnv = pkgs.python313;
        in
        {
          default = pkgs.writeShellScriptBin "scheme-langserver-bridge" ''
            export PYTHONPATH="${self}/src:$PYTHONPATH"
            exec ${pythonEnv}/bin/python3 -m scheme_langserver_bridge "$@"
          '';
        });
    };
}
