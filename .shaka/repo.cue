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
    handAuthoredWorkflows: ["validate.yml"]
}
