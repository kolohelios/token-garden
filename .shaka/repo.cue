package repopolicy

// Local workflow accounting only. No recipe applies remote repository settings.
#RepoPolicy & {
    defaultBranch: "main"
    merge: {
        rebase: true
        merge: false
        squash: true
        deleteBranchOnMerge: true
        autoMerge: false
    }
    branchProtection: {
        requiredChecks: [
            "Gate",
            "Python and Shaka checks (ubuntu-24.04)",
            "Python and Shaka checks (macos-15)",
        ]
        strictStatusChecks: true
        allowForcePush: false
        allowDeletion: false
    }
    handAuthoredWorkflows: ["validate.yml"]
}
