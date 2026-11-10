import { createContext, useContext } from 'react'

const WorkspaceTaskContext = createContext(() => {})

export const WorkspaceTaskProvider = WorkspaceTaskContext.Provider
export const useWorkspaceTask = () => useContext(WorkspaceTaskContext)
