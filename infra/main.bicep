@description('Azure region for the workload resources.')
param location string = resourceGroup().location

@description('Globally unique lowercase prefix.')
@minLength(3)
@maxLength(12)
param prefix string

@description('Existing Azure OpenAI resource ID used for RBAC.')
param azureOpenAIResourceId string

@description('Azure OpenAI endpoint, for example https://name.openai.azure.com.')
param azureOpenAIEndpoint string

@description('Azure OpenAI deployment name.')
param azureOpenAIDeployment string

@description('Web application URL to test.')
param targetUrl string

@description('QA objective passed to the job.')
param objective string = 'Validate the critical user journey and report regressions.'

@description('ACA scheduled job cron expression. Container Apps evaluates this in UTC.')
param cronExpression string = '0 18 * * *'

@description('Container image tag deployed to the new ACR.')
param imageTag string = 'latest'

@description('Globally unique storage account name. Lowercase letters and numbers only.')
@minLength(3)
@maxLength(24)
param storageAccountName string

var identityName = '${prefix}-qa-id'
var environmentName = '${prefix}-qa-env'
var jobName = '${prefix}-qa-job'
var acrName = toLower(replace('${prefix}qaacr', '-', ''))
var workspaceName = '${prefix}-qa-law'
var azureOpenAIIdParts = split(azureOpenAIResourceId, '/')

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  properties: {
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource environment 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource artifactContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'qa-artifacts'
  properties: {
    publicAccess: 'None'
  }
}

resource job 'Microsoft.App/jobs@2025-07-01' = {
  name: jobName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 7200
      replicaRetryLimit: 1
      scheduleTriggerConfig: {
        cronExpression: cronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'qa'
          image: '${acr.properties.loginServer}/maf-playwright-qa:${imageTag}'
          resources: {
            cpu: json('2.0')
            memory: '4Gi'
          }
          env: [
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'MAF_QA_AZURE_OPENAI_ENDPOINT', value: azureOpenAIEndpoint }
            { name: 'MAF_QA_AZURE_OPENAI_DEPLOYMENT', value: azureOpenAIDeployment }
            { name: 'MAF_QA_TARGET_URL', value: targetUrl }
            { name: 'MAF_QA_OBJECTIVE', value: objective }
            { name: 'MAF_QA_BLOB_ACCOUNT_URL', value: 'https://${storage.name}.blob.${az.environment().suffixes.storage}' }
            { name: 'MAF_QA_BLOB_CONTAINER', value: artifactContainer.name }
            { name: 'MAF_QA_PLAYWRIGHT_HEADLESS', value: 'true' }
          ]
        }
      ]
    }
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, 'AcrPull')
  scope: acr
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

resource blobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, 'StorageBlobDataContributor')
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
    )
  }
}

module openAIRbac './openai-rbac.bicep' = {
  name: 'openai-rbac-${uniqueString(identity.id, azureOpenAIResourceId)}'
  scope: resourceGroup(azureOpenAIIdParts[2], azureOpenAIIdParts[4])
  params: {
    accountName: azureOpenAIIdParts[8]
    principalId: identity.properties.principalId
  }
}

output acrLoginServer string = acr.properties.loginServer
output jobResourceId string = job.id
output managedIdentityClientId string = identity.properties.clientId
