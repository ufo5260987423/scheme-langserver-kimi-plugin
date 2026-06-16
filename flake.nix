{
  description = "MCP bridge connecting Kimi Code CLI to scheme-langserver";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;

      # Build the Python package for a given system
      mkPackage = system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        pkgs.python313Packages.buildPythonApplication {
          pname = "scheme-langserver-bridge";
          version = "0.1.0";
          pyproject = true;

          src = self;

          build-system = [ pkgs.python313Packages.hatchling ];

          propagatedBuildInputs = [
            pkgs.python313Packages.mcp
          ];

          nativeCheckInputs = [
            pkgs.python313Packages.pytest
            pkgs.python313Packages.pytest-asyncio
          ];

          # We don't have scheme-langserver in the check environment,
          # so skip integration tests that require it.
          pytestFlagsArray = [
            "-k"
            "not integration"
          ];

          meta = {
            description = "MCP bridge for scheme-langserver to assist Kimi with Scheme code";
            license = pkgs.lib.licenses.mit;
            mainProgram = "scheme-langserver-bridge";
          };
        };
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.python313
              pkgs.uv
            ];

            shellHook = ''
              echo "scheme-langserver-bridge dev shell"
              echo "Python: $(python3 --version)"
              echo "uv: $(uv --version)"
              echo ""
              echo "scheme-langserver is NOT bundled in this shell."
              echo "You can:"
              echo "  - install it via nixpkgs: nix shell nixpkgs#scheme-langserver"
              echo "  - let the bridge auto-download it on first use (default behavior)"
              echo "  - set SCHEME_LANGSERVER_PATH or pass langserver_path to lsp_initialize"
            '';
          };
        });

      packages = forAllSystems (system: {
        default = mkPackage system;
        scheme-langserver-bridge = mkPackage system;
      });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/scheme-langserver-bridge";
        };
        scheme-langserver-bridge = {
          type = "app";
          program = "${self.packages.${system}.scheme-langserver-bridge}/bin/scheme-langserver-bridge";
        };
      });
    };
}
