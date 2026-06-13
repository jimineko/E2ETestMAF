targetScope = 'resourceGroup'

param accountName string
param principalId string

resource openAI 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: accountName
}

resource openAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAI.id, principalId, 'CognitiveServicesOpenAIUser')
  scope: openAI
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
    )
  }
}
